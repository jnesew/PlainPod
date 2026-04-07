import sys, time
from PySide6.QtCore import QCoreApplication, ClassInfo, Property
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

@ClassInfo(name="D-Bus Interface", value="org.mpris.MediaPlayer2")
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
print(bus.registerService("org.mpris.MediaPlayer2.test1234"))
print(bus.registerObject("/org/mpris/MediaPlayer2", svc))

import threading
def introspect():
    import subprocess
    time.sleep(1)
    res = subprocess.run(["busctl", "--user", "introspect", "org.mpris.MediaPlayer2.test1234", "/org/mpris/MediaPlayer2"], capture_output=True, text=True)
    print("INTROSPECT RESULT:")
    if "org.mpris.MediaPlayer2" in res.stdout:
        print("YES! FOUND org.mpris.MediaPlayer2!")
    else:
        print("NO! Not found!")
    print(res.stdout)
    app.quit()

threading.Thread(target=introspect).start()
sys.exit(app.exec())
