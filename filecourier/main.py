import sys
from PyQt6.QtWidgets import QApplication
from app_window import FileCourierApp

def main():
    """程序主入口"""
    app = QApplication(sys.argv)
    ex = FileCourierApp()
    ex.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()