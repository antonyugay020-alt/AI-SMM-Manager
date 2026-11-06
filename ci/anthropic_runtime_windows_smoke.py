"""One bounded LIVE Anthropic request from native Windows Qt thread.

Not a full GUI acceptance. Does not test Ollama, all models, or end-to-end
project publication. No credentials or generated text enter retained evidence.
"""
import json
import os
import sys
from PySide6.QtCore import QCoreApplication, QThread, Signal, QTimer
from pathlib import Path

# Python launched with a script in ci/ does not automatically import sibling
# runtime modules from the application's separate source directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'AI_SMM_Work_Architecture_A75'))
from text_providers import call_text

MODEL = "anthropic/claude-sonnet-5-5"

class LiveTransport(QThread):
    completed = Signal(object)
    def run(self):
        try:
            response = call_text(
                MODEL, [{"role": "system", "content": "You are checking the model transport only."},
                        {"role": "user", "content":
                         "Reply with exactly one JSON object containing status=ok and scope=connection_probe."}],
                timeout=50, response_format="json_object"
            )
            obj = json.loads(response["message"]["content"])
            if obj.get("status") != "ok" or obj.get("scope") != "connection_probe":
                raise ValueError("Expected synthetic connection result is missing")
            self.completed.emit({"status": "PASS", "provider": response["provider"],
                                 "model": response["model"], "scope": "Qt transport only"})
        except Exception as exc:
            # Emit the class only, NEVER raw exception text/HTTP body/API key.
            self.completed.emit({"status": "BLOCKED", "error_type": type(exc).__name__,
                                 "scope": "Qt transport only"})

def main():
    app = QCoreApplication([])
    results = []
    worker = LiveTransport()
    def report(value):
        results.append(value)
        app.quit()
    worker.completed.connect(report)
    QTimer.singleShot(70_000, app.quit)
    worker.start()
    app.exec()
    if not worker.wait(5000):
        print(json.dumps({"status": "BLOCKED", "reason": "native thread did not finish"}))
        return 2
    value = results[0] if len(results) == 1 else {
        "status": "BLOCKED", "reason": "missing or repeated Qt completion"}
    print(json.dumps(value, ensure_ascii=False))
    return 0 if value["status"] == "PASS" else 2

if __name__ == "__main__":
    raise SystemExit(main())
