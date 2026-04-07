import sys, time
from PySide6.QtCore import QCoreApplication, ClassInfo, Property
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

@ClassInfo(name="D-Bus Interface", value="org.mpris.MediaPlayer2.test1234")
class TestAdaptor(QDBusAbstractAdaptor):
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
bus.registerService("org.test.123")
reg = getattr(QDBusConnection, "RegisterOption", QDBusConnection)
flags = getattr(reg, "ExportAllContents", 0x3F)
bus.registerObject("/org/mpris/MediaPlayer2", svc, flags)

import threading
def introspect():
    import subprocess
    time.sleep(1)
    res = subprocess.run(["busctl", "--user", "introspect", "org.test.123", "/org/mpris/MediaPlayer2"], capture_output=True, text=True)
    if "org.mpris.MediaPlayer2.test1234" in res.stdout:
        print("YES! FOUND CORRECT INTERFACE")
    else:
        print("NO! STILL NOT FOUND")
    app.quit()

threading.Thread(target=introspect).start()
sys.exit(app.exec())
