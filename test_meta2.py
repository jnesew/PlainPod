from PySide6.QtCore import QMetaObject, ClassInfo
from PySide6.QtDBus import QDBusAbstractAdaptor
import sys

class Test1(QDBusAbstractAdaptor):
    __classinfo__ = {"D-Bus Interface": "org.test.1"}

@ClassInfo(name="D-Bus Interface", value="org.test.2")
class Test2(QDBusAbstractAdaptor):
    pass

class Test3(QDBusAbstractAdaptor):
    def __init__(self):
        super().__init__()
Test3.__classinfo__ = {"D-Bus Interface": "org.test.3"}

for cls, name in [(Test1, "Test1"), (Test2, "Test2"), (Test3, "Test3")]:
    meta = cls.staticMetaObject
    found = False
    for i in range(meta.classInfoCount()):
        info = meta.classInfo(i)
        if info.name() == "D-Bus Interface":
            print(f"{name} FOUND: {info.value()}")
            found = True
    if not found:
        print(f"{name} NOT FOUND")
