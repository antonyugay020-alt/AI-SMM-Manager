import unittest

from real_workflow_acceptance import normalize_progress_event


class ProgressEventContractTests(unittest.TestCase):
    def test_transport_callback_style_event_is_normalized(self):
        event=normalize_progress_event('ollama_call',stage_name='standalone task',
                                       attempt=1,outcome='timeout')
        self.assertEqual(event,{'stage':'ollama_call','stage_name':'standalone task',
                                'attempt':1,'outcome':'timeout'})

    def test_pipeline_dictionary_event_remains_supported(self):
        event=normalize_progress_event({'stage':'draft_saved','completed':1},status='ok')
        self.assertEqual(event,{'stage':'draft_saved','completed':1,'status':'ok'})


if __name__=='__main__':
    unittest.main()
