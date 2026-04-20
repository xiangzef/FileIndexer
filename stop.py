import socket
import subprocess
import time

def check_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def get_process_on_port(port):
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
                        return int(part)
    except Exception as e:
        print(f"查询进程时出错: {e}")
    return None

def main():
    port = 56789

    if not check_port_in_use(port):
        print(f"端口 {port} 未被占用，无需关闭")
        return

    pid = get_process_on_port(port)
    if pid:
        print(f"正在终止进程 PID: {pid} on port {port}...")
        subprocess.run(['taskkill', '/F', '/PID', str(pid)])
        time.sleep(1)
        print(f"端口 {port} 已关闭")
    else:
        print(f"无法找到端口 {port} 上的进程")

if __name__ == '__main__':
    main()