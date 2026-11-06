"""Test the exact app export function with minimal Qt fakes (no Qt installation)."""
import ast
from pathlib import Path
import tempfile
import unittest


class ProjectionContractTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name).resolve()
        for folder in ('texts','pdf','diagrams'):
            (self.root/folder).mkdir()
        source=Path(__file__).with_name('app.py').read_text(encoding='utf8')
        tree=ast.parse(source)
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='export_document_files')
        self.namespace={
            'Path': Path,
            'safe_file_label':lambda s:s,
            'document_folder':lambda _: 'texts',
            'document_export_html':lambda title,content:content,
            '__import__':lambda *_args,**_kwargs:type('FakeQtGui',(),{'QPageSize':type('PageSize',(),{'A4':0,'__init__':lambda self,*args:None})}),
        }
        class Printer:
            HighResolution=1;PdfFormat=2
            def __init__(self,*args):self.destination=None
            def setOutputFormat(self,*args):pass
            def setOutputFileName(self,path):self.destination=Path(path)
            def setPageSize(self,*args):pass
        self.namespace['QPrinter']=Printer
        class TextDoc:
            invalid=False
            def setHtml(self,content):self.content=content
            def print_(self,printer):printer.destination.write_bytes(b'broken' if TextDoc.invalid else b'%PDF-1.7\n' + b'a'*40)
        self.TextDoc=TextDoc
        self.namespace['QTextDocument']=TextDoc
        code=compile(ast.Module(body=[node],type_ignores=[]),str(Path(__file__).with_name('app.py')),'exec')
        exec(code,self.namespace)
        self.export=self.namespace['export_document_files']

    def test_export_publishes_complete_validated_projection(self):
        txt,pdf,png=self.export('project','a'*32,'tasks','document','verified',None,workspace_root=self.root)
        self.assertEqual(txt.read_text(),'verified')
        self.assertTrue(pdf.read_bytes().startswith(b'%PDF-'))
        self.assertIsNone(png)

    def test_invalid_pdf_never_publishes_new_txt(self):
        txt,pdf,_=self.export('project','a'*32,'tasks','document','old',None,workspace_root=self.root)
        self.TextDoc.invalid=True
        with self.assertRaisesRegex(RuntimeError,'PDF'):
            self.export('project','a'*32,'tasks','document','new',None,workspace_root=self.root)
        self.assertEqual(txt.read_text(),'old')
        self.assertTrue(pdf.read_bytes().startswith(b'%PDF-'))
        self.TextDoc.invalid=False
        self.export('project','a'*32,'tasks','document','new',None,workspace_root=self.root)
        self.assertEqual(txt.read_text(),'new')

if __name__=='__main__':unittest.main()
