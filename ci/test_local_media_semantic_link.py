"""Committed local media resolves the original dialogue once, including restart."""
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from application_bootstrap import install_schema
from conversation_runtime import ConversationRuntime
import local_media_jobs as jobs

OWNER='a'*32
OTHER='b'*32


class SemanticLocalMediaTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('Native FFmpeg unavailable')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(self.root/'studio.sqlite');self.addCleanup(self.db.close)
        install_schema(self.db)
        for owner in (OWNER,OTHER):self.db.execute('INSERT INTO projects(id,name,category,description,created_at) VALUES(?,?,?,?,?)',(owner,'Project','','','now'))
        self.db.commit()
        self.source=self.workspace(OWNER)/'originals'/'source.mp4';self.source.parent.mkdir(parents=True)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x120:r=30',
                        '-t','0.6','-c:v','mpeg4',str(self.source)],capture_output=True,check=True,timeout=20)
        self.db.execute('INSERT INTO project_files VALUES(?,?,?,?,?,?)',('source',OWNER,'video','Source',str(self.source),'now'));self.db.commit()
        self.conversations=ConversationRuntime(self.db)
        self.turn=self.conversations.accept(OWNER,'Оставь нужный короткий фрагмент').dialogue_turn_id
        self.conversations.transition(OWNER,self.turn,'resolving')
        self.request={'task':'video_cut','sources':[str(self.source)],'start':.1,'end':.4}

    def workspace(self,owner):return self.root/owner

    def test_semantic_trim_uses_one_task_one_publication_and_resolves_original_turn(self):
        job=jobs.create_job(self.db,OWNER,self.request,dialogue_turn_id=self.turn)
        output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertEqual(self.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',(self.turn,)).fetchone()[0],'resolved')
        self.assertEqual(self.db.execute('SELECT record_id,dialogue_turn_id FROM result_publication_events').fetchone(),(job,self.turn))
        with patch.object(jobs,'_render',side_effect=AssertionError('no second render')):
            duplicate=jobs.create_job(self.db,OWNER,self.request,dialogue_turn_id=self.turn)
            self.assertEqual(duplicate,job)
            self.assertEqual(jobs.execute_job(self.db,self.workspace,OWNER,job),output)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM result_publication_events').fetchone()[0],1)
        geometry=subprocess.run(['ffprobe','-v','error','-show_entries','stream=width,height','-of','csv=p=0',output],check=True,capture_output=True,text=True,timeout=10).stdout.strip()
        self.assertEqual(geometry,'160,120')

    def test_restart_finishes_dialogue_after_artifact_commit_without_second_execution(self):
        job=jobs.create_job(self.db,OWNER,self.request,dialogue_turn_id=self.turn)
        with patch.object(jobs,'_finish_linked',side_effect=RuntimeError('crash before dialogue receipt')):
            with self.assertRaises(RuntimeError):jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertEqual(self.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',(self.turn,)).fetchone()[0],'resolving')
        with patch.object(jobs,'_render',side_effect=AssertionError('no second render')):
            jobs.recover_jobs(self.db,self.workspace,OWNER)
            self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs WHERE id=?',(job,)).fetchone(),('completed',))
            output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertTrue(Path(output).is_file())
        self.assertEqual(self.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',(self.turn,)).fetchone()[0],'resolved')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE id=?',(job,)).fetchone()[0],1)

    def test_cancellation_resolves_intake_as_failed_without_publishing_output(self):
        job=jobs.create_job(self.db,OWNER,self.request,dialogue_turn_id=self.turn)
        jobs.stop_job(self.db,OWNER,job)
        self.assertEqual(self.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',(self.turn,)).fetchone()[0],'failed')
        with self.assertRaises(jobs.LocalMediaStopped):jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE id=?',(job,)).fetchone()[0],0)

    def test_foreign_turn_or_changed_recovery_request_is_not_accepted(self):
        with self.assertRaises(ValueError):jobs.create_job(self.db,OTHER,self.request,dialogue_turn_id=self.turn)
        job=jobs.create_job(self.db,OWNER,self.request,dialogue_turn_id=self.turn)
        with self.assertRaises(ValueError):jobs.create_job(self.db,OWNER,{**self.request,'end':.5},dialogue_turn_id=self.turn)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM local_media_jobs').fetchone()[0],1)

if __name__=='__main__':unittest.main()
