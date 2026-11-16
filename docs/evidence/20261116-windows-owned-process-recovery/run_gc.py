import gc,sys,pytest
class CollectAfterEach:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self,item,nextitem):
        yield
        gc.collect()
sys.exit(pytest.main(['-q','ci','--junitxml=/workspace/work/windows-qa-20261115/forced-gc.xml'],plugins=[CollectAfterEach()]))
