from PySide6.QtCore import QMetaObject, ClassInfo
from PySide6.QtDBus import QDBusAbstractAdaptor

class Test6(QDBusAbstractAdaptor):
    __meta_classinfo__ = ClassInfo({"D-Bus Interface": "org.test.6"})

class Test7(QDBusAbstractAdaptor):
    _meta_classinfo = ClassInfo({"D-Bus Interface": "org.test.7"})

print("Test6 meta:", Test6.staticMetaObject.classInfoCount())
print("Test7 meta:", Test7.staticMetaObject.classInfoCount())
