import os
import sys
import socket
import subprocess
import time

def check_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def kill_process_on_port(port):
    try:
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        for line in result.stdout.split('\n'):
            if f':{port}' in line and 'LISTENING' in line:
                parts = line.split()
                for part in parts:
                    if part.isdigit() and part != '0':
                        pid = int(part)
                        print(f"正在终止进程 PID: {pid}")
                        subprocess.run(['taskkill', '/F', '/PID', str(pid)])
                        time.sleep(1)
                        return True
    except Exception as e:
        print(f"终止进程时出错: {e}")
    return False

def main():
    port = 56789

    if check_port_in_use(port):
        print(f"端口 {port} 已被占用，正在关闭...")
        if kill_process_on_port(port):
            print(f"端口 {port} 已释放")
            time.sleep(1)
        else:
            print(f"无法关闭端口 {port} 上的进程")
            sys.exit(1)

    print(f"正在启动服务 on http://127.0.0.1:{port}...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(script_dir, 'backend')
    main_py = os.path.join(backend_dir, 'main.py')

    subprocess.run([sys.executable, main_py], cwd=backend_dir)

if __name__ == '__main__':
    main()