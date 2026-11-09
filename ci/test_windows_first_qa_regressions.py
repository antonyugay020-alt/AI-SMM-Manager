"""First native Windows QA causes; portable tests do not attest native execution."""
import ast
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import stable_file_source as sources
from media_ingest_core import stage_source
from project_file_boundary import reject_path_link
from windows_qa_contract import source_snapshot,copy_contract_ci
from ollama_resilience import request_chat,capture_utf8_transport


class WindowsSourceTests(unittest.TestCase):
    def test_unicode_names_and_extensions_stage_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);project=root/'проект Ё 🐾';project.mkdir()
            for name in ('Фото ёлки 你好.png','Ролик №1.mp4'):
                source=root/name;source.write_bytes(bytes(range(256))*2048)
                sha,size=stage_source(project,source)
                self.assertEqual(size,source.stat().st_size)
                self.assertEqual((project/'.ingest_spool'/sha).read_bytes(),source.read_bytes())

    def test_same_metadata_content_mutation_cannot_publish(self):
        import media_ingest_core as ingest
        class ChangingReader(io.BytesIO):
            def fileno(self):return 42
            def seek(self,*args):
                self.getbuffer()[:]=b'changed!'
                return super().seek(*args)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'photo.png';source.write_bytes(b'original')
            identity=(1,2,8,4,5)
            with patch.object(ingest,'_source_identity',return_value=identity),patch.object(ingest,'descriptor_identity',return_value=identity),patch.object(ingest,'open_source',return_value=42),patch.object(ingest.os,'fdopen',return_value=ChangingReader(b'original')):
                with self.assertRaisesRegex(ValueError,'content verification'):stage_source(root,source)
            self.assertEqual(list((root/'.ingest_spool').iterdir()),[])

    def test_reparse_attribute_rejected_even_without_symlink_flag(self):
        class Attributes:st_file_attributes=0x400
        with patch.object(Path,'lstat',return_value=Attributes()):
            with self.assertRaisesRegex(ValueError,'ссылку'):reject_path_link(Path('/project/photo.png'))

    def test_windows_identity_does_not_compare_path_fstat_fields(self):
        # Backend-selection test only; actual Win32 calls covered on Windows below.
        with patch.object(sources,'_windows',return_value=True),patch.object(sources,'open_source',return_value=42),patch.object(sources,'descriptor_identity',return_value=(1,2,3,4,5)) as observed,patch.object(sources.os,'close') as closed:
            self.assertEqual(sources.source_identity(Path(__file__)),(1,2,3,4,5))
            observed.assert_called_once_with(42,Path(__file__));closed.assert_called_once_with(42)

    @unittest.skipUnless(os.name=='nt','Actual Win32 handle/share-mode check requires Windows')
    def test_native_identity_and_write_delete_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'Фото ёлки.png';path.write_bytes(b'original')
            before=sources.source_identity(path);fd=sources.open_source(path)
            try:
                self.assertEqual(before,sources.descriptor_identity(fd,path))
                with self.assertRaises(PermissionError):path.write_bytes(b'changed!')
                with self.assertRaises(PermissionError):path.unlink()
                self.assertEqual(os.read(fd,100),b'original')
                self.assertEqual(before,sources.source_identity(path))
            finally:os.close(fd)
            path.unlink() # Prove the source descriptor was released.

    @unittest.skipUnless(os.name=='nt','Actual Windows directory junction check requires Windows')
    def test_unprivileged_junction_is_rejected(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'foreign';target.mkdir();link=root/'junction'
            result=subprocess.run(['cmd','/d','/c','mklink','/J',str(link),str(target)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            try:
                (target/'image.png').write_bytes(b'image')
                with self.assertRaises(ValueError):sources.open_source(link/'image.png')
            finally:link.rmdir()
            self.assertEqual((target/'image.png').read_bytes(),b'image')

    def test_source_snapshot_keeps_packaging_authorities(self):
        inventory=source_snapshot(ROOT)['files']
        for name in ('AGENTS.md','AI_SMM_Work_Architecture_A75/AGENTS.md','AI_SMM_Work_Architecture_A75/PROJECT_VISION.md'):
            self.assertIn(name,inventory)
        self.assertIn('ci/independent_review.py',inventory)

    def test_windows_isolation_copies_entire_ci_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)
            copy_contract_ci(ROOT,target,required=True)
            source={p.relative_to(ROOT/'ci'):p.read_bytes() for p in (ROOT/'ci').rglob('*.py')}
            observed={p.relative_to(target/'ci'):p.read_bytes() for p in (target/'ci').rglob('*.py')}
            self.assertEqual(observed,source)
            self.assertIn(Path('independent_review.py'),observed)
            with self.assertRaises(RuntimeError):copy_contract_ci(target/'missing',target/'elsewhere',required=True)

    def test_sqlite_owned_handles_release_files_and_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'база Ё.sqlite'
            with closing(sqlite3.connect(path,timeout=0)) as first,closing(sqlite3.connect(path,timeout=0)) as second:
                first.execute('CREATE TABLE items(id TEXT PRIMARY KEY)');first.commit()
                first.execute('BEGIN IMMEDIATE');first.execute('INSERT INTO items VALUES (?)',('задание',))
                with self.assertRaises(sqlite3.OperationalError):second.execute('BEGIN IMMEDIATE')
                first.rollback();second.execute('BEGIN IMMEDIATE');second.commit()
            path.rename(path.with_suffix('.closed'));path.with_suffix('.closed').unlink()


class Utf8RoundtripTests(unittest.TestCase):
    def test_exact_cyrillic_request_response_and_persistence(self):
        text='Измени фон: ёлка, съёмка №1 — «Привет, мир!» 🐾 你好'
        received=[]
        def transport(req,timeout):
            self.assertIn('charset=utf-8',req.get_header('Content-type'))
            payload=json.loads(req.data.decode('utf-8',errors='strict'));received.append(payload)
            return io.BytesIO(json.dumps({'message':{'content':json.dumps({'ответ':text},ensure_ascii=False)},'done':True},ensure_ascii=False).encode('utf-8'))
        with capture_utf8_transport() as evidence,patch('urllib.request.urlopen',side_effect=transport):
            response=request_chat({'model':'local-fixture','stream':False,'messages':[{'role':'user','content':text}]},stage='UTF-8 fixture',attempts=1)
        self.assertTrue(evidence['response_utf8_valid'])
        self.assertEqual(json.loads(evidence['request_utf8']),received[0])
        self.assertEqual(received[0]['messages'][0]['content'],text)
        self.assertEqual(json.loads(response['message']['content']),{'ответ':text})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'Ответ ё.json';path.write_text(json.dumps(response,ensure_ascii=False),encoding='utf-8')
            self.assertEqual(json.loads(path.read_bytes().decode('utf-8')),response)

    def test_invalid_utf8_response_is_never_replaced_and_accepted(self):
        from provider_errors import OllamaStageError
        with capture_utf8_transport() as evidence,patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"message":{"content":"\xff"}}')):
            with self.assertRaises((OllamaStageError,UnicodeDecodeError)):
                request_chat({'model':'local-fixture','messages':[]},stage='bad encoding',attempts=1)
        self.assertFalse(evidence['response_utf8_valid'])
        self.assertIn('response_sha256',evidence)
