import json,os,shutil,sys
from pathlib import Path
root=Path('/workspace/AI-SMM-Manager');sys.path.insert(0,str(root/'AI_SMM_Work_Architecture_A75'))
import windows_acceptance as qa
from windows_qa_contract import copy_contract_ci
from execution_environment import isolated_contract_test_environment
out=Path('/workspace/work/pr4-3838bc9/isolated-final');out.mkdir(parents=True,exist_ok=True)
isolated=out/'checkout';isolated.mkdir(exist_ok=True)
app=isolated/'AI_SMM_Work_Architecture_A75';tests=isolated/'AI_SMM_Developer_A75/tests'
ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','VALIDATION_RESULTS','*.pyc')
shutil.copytree(root/'AI_SMM_Work_Architecture_A75',app,ignore=ignore,dirs_exist_ok=True)
shutil.copytree(root/'AI_SMM_Developer_A75/tests',tests,ignore=ignore,dirs_exist_ok=True)
shutil.copytree(app,tests.parent/'application',ignore=ignore,dirs_exist_ok=True)
copy_contract_ci(root,isolated,required=True)
for path in app.glob('*.py'):shutil.copy2(path,tests.parent/path.name)
qa.OUT=out
env=isolated_contract_test_environment(os.environ)
env.update(AI_SMM_TEST_ROOT=str(out/'application-data'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1')
result=qa.run_isolated_python_regression(tests,env,'full',shard_count=4)
(out/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result));sys.exit(0 if result['status']=='PASS' else 1)
