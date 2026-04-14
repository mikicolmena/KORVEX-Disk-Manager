import logging
import subprocess
from PyQt6.QtCore import QThread, pyqtSignal

class FormatWorker(QThread):
    finished = pyqtSignal(bool, str) # bool: éxito, str: mensaje
    
    def __init__(self, drive_letter, fs, label, quick):
        super().__init__()
        self.drive_letter = drive_letter
        self.fs = fs
        self.label = label
        self.quick = quick

    def run(self):
        logging.info(f"Iniciando formateo: Unidad {self.drive_letter}:, FS: {self.fs}, Label: {self.label}, Quick: {self.quick}")
        
        # PowerShell Command: Format-Volume -DriveLetter X -FileSystem NTFS -NewFileSystemLabel "Name" -Confirm:$false
        quick_flag = "-Full:$false" if self.quick else "-Full:$true"
        ps_cmd = f'Format-Volume -DriveLetter {self.drive_letter} -FileSystem {self.fs} -NewFileSystemLabel "{self.label}" {quick_flag} -Force -Confirm:$false'
        
        try:
            cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-NoProfile', '-Command', ps_cmd]
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            
            if result.returncode == 0:
                logging.info(f"Formateo exitoso para la unidad {self.drive_letter}:")
                self.finished.emit(True, f"La unidad {self.drive_letter}: se ha formateado correctamente.")
            else:
                logging.error(f"Error en formateo: {result.stderr}")
                self.finished.emit(False, f"Error al formatear:\n{result.stderr.strip()}")
                
        except Exception as e:
            logging.error(f"Excepción en formato: {e}")
            self.finished.emit(False, str(e))