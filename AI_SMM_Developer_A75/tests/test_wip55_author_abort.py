"""A model timeout terminates only this case; no blind whole-task replay."""
import unittest
from compound_retry import run_compound_with_retry
from provider_errors import AuthorExecutionAborted,OllamaStageError


class AuthorAbortContract(unittest.TestCase):
    def test_abort_is_a_typed_transport_error(self):
        self.assertTrue(issubclass(AuthorExecutionAborted,OllamaStageError))

    def test_aborted_execution_does_not_consume_retries_or_lose_partial_result(self):
        calls=[]
        first={'action':'compound_documents','instruction':'Task','documents':[
            {'kind':'strategy','valid':True,'content':'Existing checked strategy'}],
            'failure_code':'author_execution_aborted',
            'error':'Case deadline reached'}
        def executor(req,ctx,progress=None):
            calls.append(ctx.copy())
            return first
        result=run_compound_with_retry(executor,'Task',{},max_attempts=3)
        self.assertEqual(len(calls),1)
        self.assertIs(result,first)
        self.assertEqual(result['documents'][0]['content'],'Existing checked strategy')

if __name__=='__main__':unittest.main()
