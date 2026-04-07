import sys, time
from PySide6.QtCore import QCoreApplication, ClassInfo, Property
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

class TestAdaptor(QDBusAbstractAdaptor):
    ClassInfo({"D-Bus Interface": "org.mpris.MediaPlayer2"})
    def __init__(self, parent):
        super().__init__(parent)
    @Property(str)
    def Identity(self):
        return "TestApp"

class Service(sys.modules['PySide6.QtCore'].QObject):
    def __init__(self):
        super().__init__()
        self.adaptor = TestAdaptor(self)

app = QCoreApplication(sys.argv)
svc = Service()
bus = QDBusConnection.sessionBus()
print(bus.registerService("org.mpris.MediaPlayer2.test1234"))
print(bus.registerObject("/org/mpris/MediaPlayer2", svc))

import threading
def introspect():
    import subprocess
    time.sleep(1)
    res = subprocess.run(["busctl", "--user", "introspect", "org.mpris.MediaPlayer2.test1234", "/org/mpris/MediaPlayer2"], capture_output=True, text=True)
    print("INTROSPECT RESULT:")
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)
    app.quit()

threading.Thread(target=introspect).start()
sys.exit(app.exec())
