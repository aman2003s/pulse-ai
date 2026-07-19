import os
import sys
import winreg

def install_autostart():
    # Set the Run key in the Windows Registry to start Pulse silently on boot
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "PulseAssistant"
    
    # We want to run pulse.py using pythonw.exe to run headlessly (no console window)
    python_exe = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'venv', 'Scripts', 'pythonw.exe'))
    pulse_script = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'pulse.py'))
    
    command = f'"{python_exe}" "{pulse_script}"'
    
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, command)
        winreg.CloseKey(key)
        print("Autostart configured successfully.")
        print(f"Command: {command}")
    except Exception as e:
        print(f"Failed to set registry key: {e}")

if __name__ == "__main__":
    install_autostart()
