"""Native Qt budget dialog and restart persistence, no cloud/API calls."""
import sys
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton
import app
from durable_cost_gate import DurableCostGate


def main(phase):
    qapp=QApplication([])
    win=app.Studio()
    errors=[]
    try:
        def interact():
            dialog=QApplication.activeModalWidget()
            try:
                assert isinstance(dialog,QDialog), 'Engine dialog did not open'
                labels=[w.text() for w in dialog.findChildren(QLabel)]
                assert any('Месячный лимит:' in text for text in labels), labels
                buttons=[b for b in dialog.findChildren(QPushButton)
                         if 'Изменить месячный лимит' in b.text()]
                assert len(buttons)==1, 'Budget button not found'
                if phase=='configure':
                    with patch('app.QInputDialog.getDouble',return_value=(7.25,True)):
                        buttons[0].click()
                    assert DurableCostGate(app.DB).usage()['cap_usd']==7.25
                else:
                    assert DurableCostGate(app.DB).usage()['cap_usd']==7.25
                    assert any('$7.25' in text for text in
                               [w.text() for w in dialog.findChildren(QLabel)])
            except Exception as exc:
                errors.append(str(exc))
            finally:
                if dialog is not None:
                    dialog.accept()
        QTimer.singleShot(0,interact)
        win.show_engines()
        assert not errors, errors
    finally:
        assert win.close(), 'Qt window refused to close'
        qapp.processEvents()
    print('NATIVE_QT_BUDGET_'+phase.upper()+'_OK',flush=True)


if __name__=='__main__':
    main(sys.argv[1])
