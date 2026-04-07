import sys
from PySide6.QtCore import QCoreApplication, ClassInfo
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

class TestAdaptor(QDBusAbstractAdaptor):
    ClassInfo({"D-Bus Interface": "org.mpris.MediaPlayer2"})
    def __init__(self, parent):
        super().__init__(parent)

app = QCoreApplication(sys.argv)
adaptor = TestAdaptor(app)
bus = QDBusConnection.sessionBus()
bus.registerService("org.test.foo")
bus.registerObject("/foo", app)

print(bus.interface().registeredServiceNames().value())
print("Registered!")
