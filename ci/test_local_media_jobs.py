"""Real local media jobs, source ownership, receipts, restart and cancellation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from PIL import Image
import local_media_jobs as jobs

OWNER='a'*32
OTHER='b'*32


class LocalMediaJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(self.root/'studio.sqlite');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[(OWNER,),(OTHER,)])
        self.db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.commit()
        self.image=self.photo('one.png',(20,40,60))

    def workspace(self,owner):return self.root/owner

    def photo(self,name,color):
        path=self.workspace(OWNER)/'originals'/name;path.parent.mkdir(parents=True,exist_ok=True)
        with Image.new('RGB',(160,120),color) as image:image.save(path,'PNG')
        self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',(name,OWNER,name,str(path),jobs.digest_file(path),'now'));self.db.commit()
        return path

    def video(self,name,*,audio=False,color='red'):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('Native FFmpeg unavailable')
        path=self.workspace(OWNER)/'originals'/name
        command=['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=160x120:r=30']
        if audio:command+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000']
        command+=['-t','0.6','-c:v','mpeg4']
        if audio:command+=['-c:a','aac','-shortest']
        subprocess.run(command+[str(path)],check=True,capture_output=True,timeout=20)
        self.db.execute('INSERT INTO project_files VALUES(?,?,?,?,?,?)',(name,OWNER,'video',name,str(path),'now'));self.db.commit()
        return path

    def request(self,task,sources,**options):
        return {'task':task,'sources':[str(path) for path in sources],'width':128,'height':96,**options}

    def test_real_image_resize_is_project_published_and_idempotent(self):
        before=jobs.digest_file(self.image)
        job=jobs.create_job(self.db,OWNER,self.request('image_resize',[self.image]))
        output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        with Image.open(output) as image:self.assertEqual(image.size,(128,96))
        self.assertEqual(jobs.digest_file(self.image),before)
        with patch.object(jobs,'_render',side_effect=AssertionError('must reuse receipt')):
            self.assertEqual(jobs.execute_job(self.db,self.workspace,OWNER,job),output)
        self.assertEqual(self.db.execute('SELECT project_id,kind FROM project_files WHERE id=?',(job,)).fetchone(),(OWNER,'result'))

    def test_real_video_cut_and_format_preserve_source(self):
        video=self.video('clip.mp4',audio=True);before=jobs.digest_file(video)
        for task,options in [('video_cut',{'start':.1,'end':.4}),('video_format',{})]:
            job=jobs.create_job(self.db,OWNER,self.request(task,[video],**options))
            output=jobs.execute_job(self.db,self.workspace,OWNER,job)
            duration,audio=jobs._probe(output)
            self.assertTrue(audio);self.assertGreater(duration,0)
            if task=='video_cut':self.assertLess(duration,.5)
            self.assertEqual(jobs.digest_file(video),before)

    def test_real_montage_normalizes_video_and_keeps_audio_including_silent_clip(self):
        first=self.video('first.mp4',audio=True);second=self.video('second.mp4',color='blue')
        job=jobs.create_job(self.db,OWNER,self.request('video_montage',[first,second]))
        output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        duration,audio=jobs._probe(output)
        self.assertTrue(audio);self.assertGreater(duration,1);self.assertLess(duration,1.6)

    def test_real_slideshow_from_registered_photos(self):
        second=self.photo('two.png',(60,40,20))
        job=jobs.create_job(self.db,OWNER,self.request('video_from_images',[self.image,second],seconds_per_image=.5))
        output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        duration,_=jobs._probe(output);self.assertGreater(duration,.8);self.assertLess(duration,1.3)

    def test_crash_after_publication_recovers_without_rendering(self):
        job=jobs.create_job(self.db,OWNER,self.request('image_resize',[self.image]))
        output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.db.execute("UPDATE local_media_jobs SET state='running',output_path=NULL,result_sha256=NULL WHERE id=?",(job,));self.db.commit()
        jobs.recover_jobs(self.db,self.workspace,OWNER)
        with patch.object(jobs,'_render',side_effect=AssertionError('no second render')):
            self.assertEqual(jobs.execute_job(self.db,self.workspace,OWNER,job),output)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE id=?',(job,)).fetchone()[0],1)

    def test_cancelled_unpublished_task_survives_reopen_and_never_executes(self):
        job=jobs.create_job(self.db,OWNER,self.request('image_resize',[self.image]))
        jobs.stop_job(self.db,OWNER,job)
        with patch.object(jobs,'_render') as render:
            with self.assertRaises(jobs.LocalMediaStopped):jobs.execute_job(self.db,self.workspace,OWNER,job)
        render.assert_not_called();self.assertEqual(jobs.list_jobs(self.db,OWNER)[0]['state'],'cancelled')
        self.assertEqual(jobs.list_jobs(self.db,OTHER),[])

    def test_changed_input_after_failed_attempt_cannot_silently_change_task(self):
        job=jobs.create_job(self.db,OWNER,self.request('image_resize',[self.image]))
        with patch.object(jobs,'_render',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):jobs.execute_job(self.db,self.workspace,OWNER,job)
        with Image.new('RGB',(160,120),(100,100,100)) as image:image.save(self.image,'PNG')
        with self.assertRaisesRegex(ValueError,'input changed'):jobs.execute_job(self.db,self.workspace,OWNER,job)

    def test_foreign_sources_and_job_ownership_fail_before_render(self):
        job=jobs.create_job(self.db,OTHER,self.request('image_resize',[self.image]))
        with patch.object(jobs,'_render') as render:
            with self.assertRaises(ValueError):jobs.execute_job(self.db,self.workspace,OTHER,job)
            with self.assertRaises(ValueError):jobs.execute_job(self.db,self.workspace,OWNER,job)
        render.assert_not_called()

    def test_valid_but_wrong_image_output_cannot_cross_publication_boundary(self):
        job=jobs.create_job(self.db,OWNER,self.request('image_resize',[self.image]))
        def wrong(request,sources,target,check):
            with Image.new('RGB',(64,64)) as image:image.save(target,'PNG')
        with patch.object(jobs,'_render',side_effect=wrong):
            with self.assertRaisesRegex(ValueError,'dimensions differ'):jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE id=?',(job,)).fetchone()[0],0)

    def test_dialogue_receipt_hash_rejects_changed_source_before_first_render(self):
        request=self.request('image_resize',[self.image]);request['expected_sha256']=[jobs.digest_file(self.image)]
        job=jobs.create_job(self.db,OWNER,request)
        with Image.new('RGB',(160,120),(200,100,50)) as image:image.save(self.image,'PNG')
        with patch.object(jobs,'_render') as render:
            with self.assertRaisesRegex(ValueError,'dialogue receipt'):jobs.execute_job(self.db,self.workspace,OWNER,job)
        render.assert_not_called()

    def test_recovery_skips_live_process_owner_then_recovers_after_process_death(self):
        job=jobs.create_job(self.db,OWNER,self.request('image_resize',[self.image]))
        self.db.execute("UPDATE local_media_jobs SET state='running' WHERE id=?",(job,));self.db.commit()
        code="""import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from local_media_jobs import _job_lock
with _job_lock(lambda owner:Path(sys.argv[2]),sys.argv[3],sys.argv[4]) as acquired:
    assert acquired
    print('locked',flush=True)
    time.sleep(30)
"""
        process=subprocess.Popen([sys.executable,'-c',code,str(Path(jobs.__file__).parent),
            str(self.workspace(OWNER)),OWNER,job],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'locked')
            jobs.recover_jobs(self.db,self.workspace,OWNER)
            self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs WHERE id=?',(job,)).fetchone(),('running',))
            with self.assertRaisesRegex(RuntimeError,'live owner'):
                jobs.execute_job(self.db,self.workspace,OWNER,job)
            process.terminate();process.wait(timeout=5)
            jobs.recover_jobs(self.db,self.workspace,OWNER)
            self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs WHERE id=?',(job,)).fetchone(),('interrupted',))
            self.assertTrue(Path(jobs.execute_job(self.db,self.workspace,OWNER,job)).is_file())
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close();process.stderr.close()

    def test_owned_subprocess_is_terminated_when_stop_is_requested(self):
        processes=[];native=jobs.subprocess.Popen
        def spawn(*args,**kwargs):
            process=native(*args,**kwargs);processes.append(process);return process
        started=time.monotonic()
        def stop():
            if time.monotonic()-started>.1:raise jobs.LocalMediaStopped('fixture cancellation')
        with patch.object(jobs.subprocess,'Popen',side_effect=spawn):
            with self.assertRaises(jobs.LocalMediaStopped):
                jobs._tool([sys.executable,'-c','import time; time.sleep(30)'],stop,timeout=5)
        self.assertEqual(len(processes),1);self.assertIsNotNone(processes[0].poll())
        self.assertLess(time.monotonic()-started,3)

if __name__=='__main__':unittest.main()
