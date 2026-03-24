import win32serviceutil
import win32service
import win32event
import servicemanager
import subprocess
import os
import time

class DjangoService(win32serviceutil.ServiceFramework):
    _svc_name_ = "DjangoLANService"
    _svc_display_name_ = "Django LAN Web Service"
    _svc_description_ = "Django application using Waitress (LAN)"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.process:
            self.process.terminate()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("Starting Django Waitress Service")

        project_dir = r"C:\Users\medle\Documents\pys\myproject"
        python_exe = r"C:\Users\medle\Documents\pys\myenv\Scripts\python.exe"

        self.process = subprocess.Popen(
            [python_exe, "waitress_server.py"],
            cwd=project_dir
        )

        # Tell Windows: service started successfully
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)

        # Keep service alive
        while True:
            rc = win32event.WaitForSingleObject(self.stop_event, 5000)
            if rc == win32event.WAIT_OBJECT_0:
                break

if __name__ == '__main__':
    win32serviceutil.HandleCommandLine(DjangoService)
