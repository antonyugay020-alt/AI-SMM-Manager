import gc,sys,pytest
class CollectAfterEach:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self,item,nextitem):
        yield
        gc.collect()
sys.exit(pytest.main(['-q','ci','--junitxml=/workspace/work/windows-first-qa/ci-gc.xml'],plugins=[CollectAfterEach()]))
