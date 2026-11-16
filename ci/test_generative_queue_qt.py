"""Real Qt GUI lifecycle with a clearly labelled generative renderer fixture."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_local_media_dispatcher_qt as local_fixture
from PIL import Image
import local_media_jobs as jobs
from generative_models import digest


class GenerativeQueueQtTests(unittest.TestCase):
    setUpClass=classmethod(local_fixture.LocalQueueQtTests.setUpClass.__func__)
    dispose_view=local_fixture.LocalQueueQtTests.dispose_view
    drain=local_fixture.LocalQueueQtTests.drain

    def setUp(self):
        self.environment=patch.dict(os.environ,{'AI_SMM_GENERATIVE_CONFIG':''});self.environment.start();self.addCleanup(self.environment.stop)
        local_fixture.LocalQueueQtTests.setUp(self)
        bundle=self.root/'bundle';(bundle/'model').mkdir(parents=True)
        (bundle/'model/model_index.json').write_text('{}')
        manifest={'version':1,'provenance':{'fixture':True},'files':{'model/model_index.json':{'sha256':digest(bundle/'model/model_index.json'),'bytes':2}}}
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        self.config=self.root/'models.json';self.profile={'engine':'instruct_pix2pix','bundle':str(bundle),'manifest_sha256':digest(bundle/'manifest.json'),'device':'cpu'}
        self.config.write_text(json.dumps({'version':1,'profiles':{'instruction':self.profile}}))

    def fixture(self,request,sources,target,check):
        check()
        with Image.new('RGB',(request['width'],request['height']),(70,80,90)) as image:image.save(target,'PNG')
        (target.parent/'generation_receipt.json').write_text(json.dumps({**{k:request['generation'][k] for k in ('engine','manifest_sha256','device')},'seed':request['seed'],'dtype':'fixture'}))

    def test_gui_loads_persists_profile_and_runs_owned_generation(self):
        with patch('kie_media_queue_ui.QFileDialog.getOpenFileName',return_value=(str(self.config),'')):
            self.view.load_local_profiles()
        self.assertTrue(self.view.local_generation_button.isEnabled())
        self.assertEqual(self.db.execute("SELECT value FROM local_generative_settings WHERE key='config_path'").fetchone(),(str(self.config),))
        self.view.sources.item(0).setSelected(True);self.view.prompt.setPlainText('Change the selected source style')
        self.view.local_width.setValue(64);self.view.local_height.setValue(64)
        with patch.object(jobs,'_render',side_effect=self.fixture):self.view.execute_generation();self.drain()
        self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs').fetchone(),('completed',))
        request=json.loads(self.db.execute('SELECT request_json FROM local_media_jobs').fetchone()[0])
        self.assertEqual(request['generation'],self.profile);self.assertEqual(request['task'],'image_instruction')
        self.assertEqual(self.db.execute('SELECT project_id FROM project_files').fetchone(),('a'*32,))
        self.assertIn('local/instruct_pix2pix',self.view.table.item(0,1).text())

if __name__=='__main__':unittest.main()
