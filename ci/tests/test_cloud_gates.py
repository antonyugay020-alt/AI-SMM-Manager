from __future__ import annotations
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from verify_tree import verify, files_snapshot, ordered_snapshot_paths, ROOT
from run_regression import shard_modules
from independent_review import parse_verdict, split_review_diff
from aggregate_regression import assess
from protect_qa import PROTECTED_PREFIXES


def test_source_tree_is_complete_and_mirrored():
    outcome=verify()
    assert outcome['status']=='PASS',outcome
    assert outcome['source_python_modules'] >= 120
    assert outcome['developer_test_modules'] >= 250


def test_source_fingerprint_deterministic():
    assert files_snapshot()[0] == files_snapshot()[0]


def test_fingerprint_order_is_case_sensitive_on_every_platform():
    # Exact Git path spelling determines a source identity, not Windows'
    # case-insensitive pathlib comparison. These names are intentionally
    # different under POSIX ordering and Windows casefold ordering.
    names=['sample/a.py','sample/Z.py','sample/b.py']
    actual=[p.relative_to(ROOT).as_posix() for p in ordered_snapshot_paths(
        [ROOT/name for name in names])]
    assert actual == sorted(names)
    assert actual != sorted(names,key=str.casefold)


def test_four_shards_cover_each_test_once():
    cases=[shard_modules(4,i)[0] for i in range(4)]
    assert len({a for group in cases for a in group}) == sum(map(len,cases))
    assert sum(map(len,cases)) >=250


@pytest.mark.parametrize('raw',[
    'not-json',
    '{"verdict":"APPROVE","critical_findings":[{"file":"x.py","reason":"bad"}],"confidence":"high"}',
    '{"verdict":"APPROVE","critical_findings":[],"confidence":"uncertain"}',
    '{"verdict":"REJECT","critical_findings":"bad","confidence":"low"}',
    '{}',
])
def test_review_rejects_invalid_or_inconsistent_payloads(raw):
    with pytest.raises((ValueError,TypeError)):
        parse_verdict(raw)


def test_review_partition_preserves_entire_large_diff():
    change1='diff --git a/a.py b/a.py\n'+('+'+'hello\n')*400
    change2='diff --git a/b.py b/b.py\n'+('+'+'world\n')*400
    original=change1+change2
    chunks=split_review_diff(original,max_chars=1900,max_parts=8)
    assert len(chunks)>2
    assert all(len(item)<=1900 for item in chunks)
    assert ''.join(chunks)==original
    assert original.count('diff --git') == 2


def test_review_partition_fails_closed_on_missing_or_excessive_data():
    with pytest.raises(ValueError):split_review_diff('')
    with pytest.raises(ValueError):split_review_diff('diff --git a/a b/a\n'+'+'+'A'*20000,
                                                  max_chars=1000,max_parts=3)


def test_review_accepts_only_valid_explicit_approval():
    result=parse_verdict('{"verdict":"APPROVE","critical_findings":[],"confidence":"high"}')
    assert result['verdict']=='APPROVE'


def sample_evidence(tmp_path,change_fingerprint=False,missing=False):
    paths=[]
    for platform in ('linux','win32'):
        for shard in range(4):
            if missing and platform=='win32' and shard==3:continue
            report={
                'status':'PASS','platform':platform,'shard':shard,'shards':4,
                'module_count':64,'total_module_inventory':256,
                'source_fingerprint':'DIFFERENT' if change_fingerprint and platform=='win32' and shard==2 else 'FIXED',
                'counts':{'tests':200,'errors':0,'failures':0,'skipped':0}}
            path=tmp_path/f'{platform}_shard_{shard}.json'
            path.write_text(__import__('json').dumps(report),encoding='utf-8')
            paths.append(path)
    return paths


def test_aggregate_passes_only_complete_cross_platform_evidence(tmp_path):
    assert assess(sample_evidence(tmp_path))['status']=='PASS'


def test_aggregate_blocks_missing_windows_shard(tmp_path):
    assert assess(sample_evidence(tmp_path,missing=True))['status']=='BLOCKED'


def test_aggregate_blocks_source_fingerprint_mismatch(tmp_path):
    assert assess(sample_evidence(tmp_path,change_fingerprint=True))['status']=='BLOCKED'


def test_qa_authority_is_in_protected_paths():
    required=('ci/','.github/','AI_SMM_10_FULL_WINDOWS_QA.py',
              'AI_SMM_Work_Architecture_A75/windows_acceptance.py')
    for item in required:assert item in PROTECTED_PREFIXES


def test_clean_candidate_uses_actual_release_guard_without_release_claim(tmp_path):
    from build_candidate import build
    report=build(tmp_path/'qa_candidate')
    assert report['status']=='PASS'
    assert report['release_status']=='NOT_RELEASE'
    assert Path(report['archive']).is_file()
    assert Path(report['clean_candidate'],'PACKAGE_MANIFEST.json').is_file()