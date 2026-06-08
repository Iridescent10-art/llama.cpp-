# -*- coding: utf-8 -*-
# 佩丽卡监督.pyw
# LLM Launcher - 佩丽卡监督 v6.6

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import subprocess
import threading
import os
import re
import json
import ctypes
import time
import urllib.request
import socket
from pathlib import Path
from datetime import datetime
import atexit
import sys

# ════════════════════════════════════════════════════════════════
#  隐藏控制台窗口
# ════════════════════════════════════════════════════════════════
if os.name == "nt":
    try:
        console_window = ctypes.windll.kernel32.GetConsoleWindow()
        if console_window:
            ctypes.windll.user32.ShowWindow(console_window, 0)
    except Exception:
        pass


def _global_cleanup():
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "llama-server.exe"],
            capture_output=True, timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
    except Exception:
        pass


atexit.register(_global_cleanup)


def get_app_dir():
    if getattr(sys, 'frozen', False):
        app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        config_dir = app_data / "Pelicar"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir
    else:
        return Path(__file__).parent


def get_exe_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent


def _check_path_for_chinese(path_str):
    if not path_str:
        return False, ""
    path_str = str(path_str).strip()
    if not path_str:
        return False, ""
    try:
        path_str.encode('ascii')
        return False, ""
    except UnicodeEncodeError:
        non_ascii_chars = set()
        for char in path_str:
            try:
                char.encode('ascii')
            except UnicodeEncodeError:
                non_ascii_chars.add(char)
        chars_display = ", ".join(sorted(non_ascii_chars)[:5])
        if len(non_ascii_chars) > 5:
            chars_display += "..."
        error_msg = (
            f"路径包含中文或特殊字符: {chars_display}\n\n"
            f"llama-server 不支持中文路径，请将文件夹/文件名改为英文。\n\n"
            f"当前路径:\n{path_str}\n\n"
            f"建议修改为纯英文路径，例如:\n"
            f"  D:\\AI\\models\\  而不是  D:\\AI\\模型\\\n"
            f"  D:\\AI\\llama\\   而不是  D:\\AI\\拉马\\"
        )
        return True, error_msg


def _get_cpu_thread_count():
    try:
        count = os.cpu_count()
        if count and count > 0:
            return count
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-CimInstance Win32_Processor).NumberOfLogicalProcessors"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        if result.returncode == 0:
            val = result.stdout.strip()
            if val and val.isdigit():
                return int(val)
    except Exception:
        pass
    return None


def _get_local_ips():
    ips = ["127.0.0.1", "localhost"]
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips:
            ips.insert(2, ip)
    except Exception:
        pass
    return ips


# ════════════════════════════════════════════════════════════════
#  主启动器类
# ════════════════════════════════════════════════════════════════
class LLMauncher:
    def __init__(self):
        self.process = None
        self.process_pid = None
        self.base_dir = get_exe_dir()
        self.config_dir = get_app_dir()
        self.config_file = self.config_dir / "launcher_config.json"

        self.config = {
            "server_path": str(self.base_dir / "llama-server.exe"),
            "models_dir": str(self.base_dir / "models"),
            "model_path": "",
            "mmproj_path": "",
            "gpu_layers": 60,
            "context_size": 65536,
            "batch_size": 4096,
            "threads": 12,
            "port": 8080,
            "flash_attention": True,
            "kv_compress": True,
            "kv_type": "q8_0",
            "reasoning": "off",
            "parallel": 1,
            "speed_display_time": 20,
        }

        self.model_files = []
        self.mmproj_files = []
        self.gen_speed_samples = []
        self.prompt_speed_samples = []
        self.session_total_tokens = 0
        self.session_start_time = None
        self.server_ready = False
        self._stopping = False
        self._generating = False
        self._gen_dot_count = 0
        self._gpu_monitoring = False
        self._cleanup_done = False
        self._gpu_warning_shown = False
        self._is_running = False
        self._vram_after_id = None
        self._vram_color = "#4ec9b0"

        # 资源监控数据
        self._cpu_usage = None
        self._gpu_info = None
        self._sys_mem = None
        self._proc_mem = None

        atexit.register(self._emergency_cleanup)
        self.load_config()
        self.setup_ui()
        self.load_models()
        self.root.after(200, self._check_first_run)

    def _emergency_cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._stopping = True
        self._gpu_monitoring = False
        pid = self.process_pid
        if pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
            except Exception:
                pass
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "llama-server.exe"],
                capture_output=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
        except Exception:
            pass

    def load_config(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
        except Exception:
            pass

    def save_config(self):
        try:
            self.config["server_path"] = self.server_var.get().strip()
            self.config["models_dir"] = self.dir_var.get().strip()
            self.config["model_path"] = self.model_path_var.get().strip()
            self.config["mmproj_path"] = self.mmproj_path_var.get().strip()
            self.config["gpu_layers"] = self.gpu_var.get()
            self.config["context_size"] = self.ctx_var.get()
            self.config["batch_size"] = self.batch_var.get()
            self.config["threads"] = self.thread_var.get()
            self.config["port"] = self.port_var.get()
            self.config["flash_attention"] = self.fa_var.get()
            self.config["kv_compress"] = self.kv_var.get()
            self.config["kv_type"] = self.kv_type_var.get()
            self.config["reasoning"] = self.reasoning_var.get()
            self.config["parallel"] = self.parallel_var.get()
            self.config["speed_display_time"] = self.speed_time_var.get()
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def reset_to_default(self):
        result = messagebox.askyesno(
            "恢复默认设置",
            "确定要恢复所有设置为默认值吗？\n\n"
            "这将重置所有性能参数和路径设置，\n"
            "但不会删除你的模型文件。\n\n"
            "当前配置将丢失！",
            icon='warning'
        )
        if result:
            self.config = {
                "server_path": str(self.base_dir / "llama-server.exe"),
                "models_dir": str(self.base_dir / "models"),
                "model_path": "",
                "mmproj_path": "",
                "gpu_layers": 60,
                "context_size": 65536,
                "batch_size": 4096,
                "threads": 12,
                "port": 8080,
                "flash_attention": True,
                "kv_compress": True,
                "kv_type": "q8_0",
                "reasoning": "off",
                "parallel": 1,
                "speed_display_time": 20,
            }

            self.server_var.set(self.config["server_path"])
            self.dir_var.set(self.config["models_dir"])
            self.model_path_var.set(self.config["model_path"])
            self.mmproj_path_var.set(self.config["mmproj_path"])
            self.gpu_var.set(self.config["gpu_layers"])
            self.ctx_var.set(self.config["context_size"])
            self.batch_var.set(self.config["batch_size"])
            self.thread_var.set(self.config["threads"])
            self.port_var.set(self.config["port"])
            self.fa_var.set(self.config["flash_attention"])
            self.kv_var.set(self.config["kv_compress"])
            self.kv_type_var.set(self.config["kv_type"])
            self.reasoning_var.set(self.config["reasoning"])
            self.parallel_var.set(self.config["parallel"])
            self.speed_time_var.set(self.config["speed_display_time"])

            self.save_config()
            self.load_models()
            self.log_msg("已恢复默认设置", "info")
            self._update_vram_estimate()
            messagebox.showinfo("完成", "已恢复默认设置！")

    # ════════════════════════════════════════════════════════════════
    #  锁定/解锁参数控件
    # ════════════════════════════════════════════════════════════════

    def _set_controls_state(self, state):
        """设置所有配置控件的状态 (normal/disabled)"""
        for widget in self.srv_frame.winfo_children():
            for w in widget.winfo_children():
                try:
                    w.config(state=state)
                except:
                    pass

        for widget in self.dir_frame.winfo_children():
            for w in widget.winfo_children():
                try:
                    w.config(state=state)
                except:
                    pass

        try:
            self.combo.config(state="readonly" if state == "normal" else "disabled")
        except:
            pass

        try:
            self.mmproj_combo.config(state="readonly" if state == "normal" else "disabled")
        except:
            pass

        spinboxes = [
            self.gpu_spinbox, self.ctx_spinbox, self.thread_spinbox,
            self.batch_spinbox, self.port_spinbox, self.parallel_spinbox,
            self.speed_time_spinbox
        ]
        for sb in spinboxes:
            try:
                sb.config(state=state)
            except:
                pass

        try:
            self.reasoning_combo.config(state="readonly" if state == "normal" else "disabled")
        except:
            pass

        try:
            self.kv_type_combo.config(state="readonly" if state == "normal" else "disabled")
        except:
            pass

        checkboxes = [self.fa_check, self.kv_check]
        for cb in checkboxes:
            try:
                cb.config(state=state)
            except:
                pass

        help_buttons = [self.gpu_help_btn, self.thread_help_btn, self.kv_help_btn]
        for btn in help_buttons:
            try:
                btn.config(state=state)
            except:
                pass

        browse_buttons = [self.srv_browse_btn, self.dir_browse_btn, self.single_model_btn, self.mmproj_clear_btn]
        for btn in browse_buttons:
            try:
                btn.config(state=state)
            except:
             pass

        try:
            self.refresh_btn.config(state=state)
        except:
            pass

    # ════════════════════════════════════════════════════════════════
    #  首次运行引导
    # ════════════════════════════════════════════════════════════════

    def _check_first_run(self):
        server = self.server_var.get().strip()
        models = self.dir_var.get().strip()
        need_server = not server or not os.path.isfile(server)
        need_models = not models or not os.path.isdir(models)
        if need_server:
            self._guide_find_server()
        elif need_models:
            self._guide_find_models()

    def _guide_find_server(self):
        result = messagebox.askyesno(
            "欢迎使用佩丽卡监督",
            "👋 欢迎使用佩丽卡监督！\n\n"
            "第一步：请找到 llama-server.exe 文件\n\n"
            "llama-server.exe 是 llama.cpp 的服务器程序，\n"
            "用于运行 GGUF 格式的大语言模型。\n\n"
            "如果你还没有下载 llama.cpp，可以从这里获取：\n"
            "https://github.com/ggml-org/llama.cpp/releases\n\n"
            "点击「是」开始选择 llama-server.exe 文件\n"
            "点击「否」稍后手动设置",
            icon='info'
        )
        if result:
            self.browse_server()
        self.root.after(500, self._guide_find_models)

    def _guide_find_models(self):
        models = self.dir_var.get().strip()
        if models and os.path.isdir(models):
            gguf_files = list(Path(models).rglob("*.gguf"))
            if gguf_files:
                return
        result = messagebox.askyesno(
            "设置模型文件夹",
            "第二步：请找到存放 AI 模型的文件夹\n\n"
            "模型文件夹是存放 .gguf 格式模型文件的目录。\n\n"
            "如果你还没有下载模型，推荐从这里获取：\n"
            "• https://huggingface.co（搜索 GGUF）\n"
            "• https://modelscope.cn（国内镜像）\n\n"
            "⚠️ 注意：文件夹路径不能包含中文！\n"
            "例如：D:\\AI\\models\\ （正确）\n"
            "      D:\\AI\\模型\\ （错误）\n\n"
            "点击「是」选择模型文件夹\n"
            "点击「否」稍后手动设置",
            icon='info'
        )
        if result:
            self.browse_dir()

    # ════════════════════════════════════════════════════════════════
    #  系统监控 + 警告实时刷新
    # ════════════════════════════════════════════════════════════════

    def _get_gpu_info(self):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu,fan.speed,power.draw",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            if result.returncode == 0:
                line = result.stdout.strip().split('\n')[0]
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 6:
                    return {
                        "mem_used": float(parts[0]) if parts[0] else 0,
                        "mem_total": float(parts[1]) if parts[1] else 0,
                        "gpu_util": float(parts[2]) if parts[2] else 0,
                        "temp": float(parts[3]) if parts[3] else 0,
                        "fan_speed": float(parts[4]) if parts[4] else 0,
                        "power_draw": float(parts[5]) if parts[5] else 0,
                    }
        except Exception:
            pass
        return None

    def _get_system_memory(self):
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return {
                "total": stat.ullTotalPhys / (1024 ** 3),
                "used": (stat.ullTotalPhys - stat.ullAvailPhys) / (1024 ** 3),
                "available": stat.ullAvailPhys / (1024 ** 3),
                "percent": stat.dwMemoryLoad
            }
        except Exception:
            pass
        return None

    def _get_cpu_usage(self):
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            if result.returncode == 0:
                val = result.stdout.strip()
                if val and val.isdigit():
                    return float(val)
        except Exception:
            pass
        return None

    def _get_cpu_temp(self):
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "(Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace 'root/wmi').CurrentTemperature / 10 - 273.15"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            if result.returncode == 0:
                val = result.stdout.strip()
                if val and val.replace('.', '').isdigit():
                    return float(val)
        except Exception:
            pass
        return None

    def _get_process_memory(self):
        if not self.process_pid:
            return None
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 f"(Get-Process -Id {self.process_pid}).WorkingSet64 / 1MB"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            if result.returncode == 0:
                val = result.stdout.strip()
                if val:
                    return float(val)
        except Exception:
            pass
        return None

    def start_resource_monitor(self):
        self._gpu_monitoring = True
        self._update_resource_display()

    def stop_resource_monitor(self):
        self._gpu_monitoring = False

    def _update_resource_display(self):
        if not self._gpu_monitoring:
            return

        def _fetch():
            self._cpu_usage = self._get_cpu_usage()
            self._gpu_info = self._get_gpu_info()
            self._sys_mem = self._get_system_memory()
            self._proc_mem = self._get_process_memory()
            cpu_temp = self._get_cpu_temp()

            def _update():
                if self._cpu_usage is not None:
                    cpu_text = f"CPU 占用:  {self._cpu_usage:.0f}%"
                    if cpu_temp:
                        cpu_text += f"  |  温度: {cpu_temp:.0f}°C"
                    self.cpu_info_var.set(cpu_text)
                    if self._cpu_usage > 95 or (cpu_temp and cpu_temp > 90):
                        self.cpu_label.config(fg="#f44747")
                    elif self._cpu_usage > 80 or (cpu_temp and cpu_temp > 80):
                        self.cpu_label.config(fg="#ffcc00")
                    else:
                        self.cpu_label.config(fg="#4ec9b0")
                else:
                    self.cpu_info_var.set("CPU 占用:  N/A (需管理员权限)")

                if self._gpu_info:
                    gpu = self._gpu_info
                    mem_pct = (gpu["mem_used"] / gpu["mem_total"] * 100) if gpu["mem_total"] > 0 else 0
                    gpu_line1 = f"GPU 显存:  {gpu['mem_used']:.0f} / {gpu['mem_total']:.0f} MB ({mem_pct:.0f}%)  |  利用率: {gpu['gpu_util']:.0f}%"
                    self.gpu_info1_var.set(gpu_line1)
                    gpu_parts = [f"GPU 状态:  温度: {gpu['temp']:.0f}°C"]
                    if gpu["fan_speed"]:
                        gpu_parts.append(f"风扇: {gpu['fan_speed']:.0f}%")
                    if gpu["power_draw"]:
                        gpu_parts.append(f"功耗: {gpu['power_draw']:.0f}W")
                    gpu_line2 = "  |  ".join(gpu_parts)
                    self.gpu_info2_var.set(gpu_line2)
                    if mem_pct > 90 or gpu['temp'] > 90:
                        self.gpu_label1.config(fg="#f44747")
                        self.gpu_label2.config(fg="#f44747")
                    elif mem_pct > 70 or gpu['temp'] > 80:
                        self.gpu_label1.config(fg="#ffcc00")
                        self.gpu_label2.config(fg="#ffcc00")
                    else:
                        self.gpu_label1.config(fg="#4ec9b0")
                        self.gpu_label2.config(fg="#4ec9b0")
                else:
                    self.gpu_info1_var.set("GPU 显存:  未检测到 NVIDIA GPU")
                    self.gpu_info2_var.set("")

                if self._sys_mem:
                    mem = self._sys_mem
                    self.mem_info_var.set(
                        f"系统内存:  {mem['used']:.1f} / {mem['total']:.1f} GB ({mem['percent']:.0f}%)  |  剩余: {mem['available']:.1f} GB")
                    if mem['percent'] > 90:
                        self.mem_label.config(fg="#f44747")
                    elif mem['percent'] > 70:
                        self.mem_label.config(fg="#ffcc00")
                    else:
                        self.mem_label.config(fg="#4ec9b0")
                else:
                    self.mem_info_var.set("系统内存:  N/A")

                if self._proc_mem is not None:
                    self.proc_info_var.set(f"进程内存:  {self._proc_mem:.0f} MB")
                else:
                    self.proc_info_var.set("进程内存:  N/A")

                self._update_warning_display()

            self._ui_call(_update)

        threading.Thread(target=_fetch, daemon=True).start()
        if self._gpu_monitoring:
            self.root.after(2000, self._update_resource_display)

    def _update_warning_display(self):
        """根据资源占用实时更新警告提示，给出使用建议"""
        warnings = []
        suggestions = []

        if self._cpu_usage is not None:
            if self._cpu_usage > 95:
                warnings.append("🔴 CPU 占用过高 (>95%)，系统可能卡顿")
                suggestions.append("• 减少线程数或关闭其他CPU密集型程序")
            elif self._cpu_usage > 85:
                warnings.append("🟡 CPU 占用较高 (>85%)，可能影响性能")
                suggestions.append("• 考虑减少并行数或线程数")
            elif self._cpu_usage < 10 and self.server_ready:
                warnings.append("🟢 CPU 占用很低 (<10%)，性能可能未充分利用")
                suggestions.append("• 可尝试增加线程数或批处理大小以提高速度")

        cpu_temp = self._get_cpu_temp()
        if cpu_temp:
            if cpu_temp > 90:
                warnings.append("🔴 CPU 温度过高 (>90°C)，请检查散热！")
                suggestions.append("• 清理风扇灰尘，改善机箱通风")
            elif cpu_temp > 80:
                warnings.append("🟡 CPU 温度较高 (>80°C)，注意散热")

        if self._gpu_info:
            gpu = self._gpu_info
            mem_pct = (gpu["mem_used"] / gpu["mem_total"] * 100) if gpu["mem_total"] > 0 else 0

            if mem_pct > 95:
                warnings.append("🔴 GPU 显存即将满载 (>95%)，可能导致崩溃或大幅降速")
                suggestions.append("• 降低上下文大小或GPU层数")
                suggestions.append("• 选择更小的量化版本 (如Q4_K_M)")
            elif mem_pct > 85:
                warnings.append("🟡 GPU 显存占用较高 (>85%)，显存紧张")
                suggestions.append("• 考虑降低上下文或启用KV压缩")
            elif mem_pct < 30 and self.server_ready:
                warnings.append("🟢 GPU 显存充足 (<30%)，可尝试更大模型或增加GPU层数")

            if gpu["temp"] > 90:
                warnings.append("🔴 GPU 温度过高 (>90°C)，请检查散热！")
                suggestions.append("• 提高风扇转速或改善机箱通风")
            elif gpu["temp"] > 80:
                warnings.append("🟡 GPU 温度较高 (>80°C)，注意散热")
                suggestions.append("• 可适当降低GPU功耗限制")

            if gpu["gpu_util"] < 10 and self.server_ready and not self._generating:
                warnings.append("🟢 GPU 利用率很低，当前空闲")
            elif gpu["gpu_util"] > 95:
                warnings.append("🟡 GPU 利用率很高 (>95%)，满负荷运行")

        if self._sys_mem:
            mem = self._sys_mem
            if mem['percent'] > 95:
                warnings.append("🔴 系统内存即将满载 (>95%)，可能导致程序崩溃")
                suggestions.append("• 关闭其他程序以释放内存")
                suggestions.append("• 减小上下文大小或并行数")
            elif mem['percent'] > 85:
                warnings.append("🟡 系统内存占用较高 (>85%)，内存紧张")
                suggestions.append("• 考虑关闭一些后台程序")
            elif mem['available'] < 2.0:
                warnings.append("🟡 可用内存不足 (<2GB)，可能影响性能")

        if self._proc_mem:
            if self._proc_mem > 20000:
                warnings.append("🟡 llama-server 进程内存较大 (>20GB)")
                suggestions.append("• 检查是否有内存泄漏")
                suggestions.append("• 考虑重启服务")

        if self._gpu_warning_shown:
            warnings.append("⚠️ llama-server 报告显存不足")
            suggestions.append("• 降低GPU层数或上下文大小")
            suggestions.append("• 换用更小的量化版本")

        if self.gen_speed_samples and self.gen_speed_samples[-1] < 1.0:
            warnings.append("🟡 生成速度很慢 (<1 tok/s)，可能原因:")
            suggestions.append("• GPU显存不足，部分层在CPU上运行")
            suggestions.append("• 模型太大或量化版本不适合")

        if warnings:
            warning_text = "━━━━━━━━━━━━━━ 资源状态检测 ━━━━━━━━━━━━━━\n"
            warning_text += "\n".join(warnings)

            if suggestions:
                unique_suggestions = list(dict.fromkeys(suggestions))
                warning_text += "\n\n━━━━━━━━━━━━━━ 优化建议 ━━━━━━━━━━━━━━\n"
                warning_text += "\n".join(unique_suggestions)

            has_red = any("🔴" in w for w in warnings)
            has_yellow = any("🟡" in w for w in warnings)
            has_gpu_warn = any("⚠️" in w for w in warnings)

            if has_red or has_gpu_warn:
                level = "error"
            elif has_yellow:
                level = "warn"
            else:
                level = "info"

            self._show_warning(warning_text, level)
        else:
            if self.server_ready:
                self._show_warning("✅ 所有资源状态正常，服务运行良好\n\n━━━━━━━━━━━━━━ 当前状态 ━━━━━━━━━━━━━━\n• CPU、GPU、内存使用率均在健康范围\n• 没有检测到性能瓶颈", "ok")
            else:
                self._show_warning("系统资源正常，等待启动服务\n\n━━━━━━━━━━━━━━ 使用提示 ━━━━━━━━━━━━━━\n• 首次使用请点击「启动服务」\n• 启动后可在浏览器中访问 WebUI\n• 点击「使用说明」查看详细帮助", "info")

    def _find_icon(self):
        candidates = []
        if getattr(sys, 'frozen', False):
            meipass = Path(getattr(sys, '_MEIPASS', ''))
            if meipass:
                candidates.append(meipass / "icon.ico")
        candidates.append(self.base_dir / "icon.ico")
        candidates.append(self.config_dir / "icon.ico")
        for p in candidates:
            if p.exists():
                return str(p)
        return None

    def _create_toplevel(self, *args, **kwargs):
        """创建 Toplevel 窗口并自动设置自定义图标"""
        win = tk.Toplevel(self.root, *args, **kwargs)
        icon_path = self._find_icon()
        if icon_path:
            try:
                win.iconbitmap(icon_path)
            except Exception:
                pass
        return win

    # ════════════════════════════════════════════════════════════════
    #  显存预估
    # ════════════════════════════════════════════════════════════════

    def _estimate_vram(self):
        """根据当前模型大小和参数预估显存占用"""
        model_path = self.model_path_var.get().strip()
        if not model_path or not os.path.isfile(model_path):
            self.vram_model_var.set("  模型权重:  N/A（未选择模型）")
            self.vram_kv_var.set("  KV 缓存:   N/A")
            self.vram_buf_var.set("  计算缓冲:  N/A")
            self.vram_total_var.set("合计: N/A")
            self.vram_fit_var.set("请先选择模型文件")
            self.vram_total_label.config(foreground="#888888")
            return

        # ── 文件大小 ──
        file_size_gb = os.path.getsize(model_path) / (1024 ** 3)

        # ── 根据文件大小估算总层数 ──
        if file_size_gb < 1.5:
            est_layers = 26      # 1-2B
        elif file_size_gb < 3.5:
            est_layers = 32      # 7-8B
        elif file_size_gb < 5.5:
            est_layers = 40      # 10-12B
        elif file_size_gb < 9:
            est_layers = 48      # 14B
        elif file_size_gb < 14:
            est_layers = 64      # 20-27B
        elif file_size_gb < 25:
            est_layers = 80      # 32-40B
        else:
            est_layers = 128     # 70B+

        # ── 1) 模型权重显存 ──
        ngl = self.gpu_var.get()
        actual_layers = min(ngl, est_layers)
        model_vram = file_size_gb * actual_layers / est_layers

        # ── 2) KV 缓存显存 ──
        ctx = self.ctx_var.get()
        # 基础估算: n_heads(128) × head_dim(2) × seq_len × layers × 2(K+V) × 2(fp16)
        kv_base_bytes = 128 * 2 * ctx * est_layers * 2 * 2

        kv_factors = {
            "f32": 2.0, "f16": 1.0, "bf16": 1.0,
            "q8_0": 0.5, "q5_0": 0.35, "q5_1": 0.35,
            "q4_0": 0.25, "q4_1": 0.25,
            "iq4_nl": 0.25, "turbo4": 0.25,
        }
        kv_type = self.kv_type_var.get()
        if self.kv_var.get():
            kv_vram = (kv_base_bytes * kv_factors.get(kv_type, 1.0)) / (1024 ** 3)
        else:
            kv_vram = kv_base_bytes / (1024 ** 3)

        # ── 3) 计算缓冲显存 ──
        batch = self.batch_var.get()
        parallel = self.parallel_var.get()
        buf_vram = (batch * parallel * 2 * 128 * 2) / (1024 ** 3) + 0.3

        total = model_vram + kv_vram + buf_vram

        # ── 更新显示 ──
        self.vram_model_var.set(
            f"  模型权重:  {model_vram:.1f} GB   ({file_size_gb:.1f}GB文件, GPU层 {actual_layers}/{est_layers})")
        self.vram_kv_var.set(
            f"  KV 缓存:   {kv_vram:.1f} GB   (上下文 {ctx}, {kv_type if self.kv_var.get() else 'fp16'})")
        self.vram_buf_var.set(
            f"  计算缓冲:  {buf_vram:.1f} GB   (批处理 {batch}, 并行 {parallel})")
        self.vram_total_var.set(f"合计: {total:.1f} GB")

        if total > 24:
            self.vram_fit_var.set("⚠ 超过 24GB，仅 RTX 3090/4090/5090 级别可运行")
            self._vram_color = "#f44747"
        elif total > 16:
            self.vram_fit_var.set("⚠ 需 16GB+ 显存 (RTX 4080/5070Ti/A4000)")
            self._vram_color = "#f44747"
        elif total > 12:
            self.vram_fit_var.set("需 12GB+ 显存 (RTX 4070Ti/3060-12G)")
            self._vram_color = "#ffcc00"
        elif total > 8:
            self.vram_fit_var.set("需 8GB+ 显存 (RTX 4060/3070)")
            self._vram_color = "#4ec9b0"
        elif total > 6:
            self.vram_fit_var.set("需 6GB+ 显存 (RTX 3060/2060)")
            self._vram_color = "#4ec9b0"
        else:
            self.vram_fit_var.set("大部分显卡均可运行 ✓")
            self._vram_color = "#4ec9b0"

        self.vram_total_label.config(foreground=self._vram_color)

    def _update_vram_estimate(self, *args):
        """防抖更新：参数变化后延迟200ms再计算"""
        if self._vram_after_id:
            self.root.after_cancel(self._vram_after_id)
        self._vram_after_id = self.root.after(200, self._estimate_vram)

    # ════════════════════════════════════════════════════════════════
    #  帮助弹窗
    # ════════════════════════════════════════════════════════════════

    def _show_thread_help(self):
        detected = _get_cpu_thread_count()
        win = self._create_toplevel()
        win.title("如何查看CPU线程数")
        win.geometry("520x480")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 520) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 480) // 2
        win.geometry(f"+{x}+{y}")

        main_frame = ttk.Frame(win, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(main_frame, text="❓ 如何查看CPU线程数", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))

        text_widget = tk.Text(main_frame, font=("Microsoft YaHei", 10), wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10, bg="#f5f5f5", spacing1=2, spacing3=2)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert("1.0", "线程数 ≠ 核心数，现代CPU有超线程技术\n")
        text_widget.insert(tk.END, "例如: 6核12线程的CPU，应该填 12\n\n")
        text_widget.insert(tk.END, "━━━ 方法1: 任务管理器（最简单）━━━\n")
        text_widget.insert(tk.END, "① 按 Ctrl+Shift+Esc 打开任务管理器\n")
        text_widget.insert(tk.END, "② 点击「性能」标签 → 点击「CPU」\n")
        text_widget.insert(tk.END, "③ 右上角显示「逻辑处理器: XX」\n\n")
        text_widget.insert(tk.END, "━━━ 方法2: 命令行 ━━━\n")
        text_widget.insert(tk.END, "① 按 Win+R，输入 cmd，回车\n")
        text_widget.insert(tk.END, "② 输入:\n")
        text_widget.insert(tk.END, "   wmic cpu get NumberOfLogicalProcessors\n\n")
        text_widget.insert(tk.END, "━━━ 方法3: 查看CPU型号 ━━━\n")
        text_widget.insert(tk.END, "① 右键「此电脑」→「属性」\n")
        text_widget.insert(tk.END, "② 查看处理器型号，网上搜索参数\n\n")
        text_widget.insert(tk.END, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        text_widget.insert(tk.END, "提示: 填太多会导致CPU满载卡顿\n")
        text_widget.insert(tk.END, "      填太少会浪费CPU性能")

        if detected:
            text_widget.insert(tk.END, f"\n\n🔍 自动检测: 你的CPU有 {detected} 个线程")
            text_widget.tag_add("detected", "end-1l", "end")
            text_widget.tag_config("detected", foreground="#007acc", font=("Microsoft YaHei", 10, "bold"))
        text_widget.config(state=tk.DISABLED)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        if detected:
            def auto_fill():
                self.thread_var.set(detected)
                win.destroy()
                self.log_msg(f"已自动设置线程数为 {detected}", "info")
            ttk.Button(btn_frame, text=f"自动填入 ({detected})", command=auto_fill).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="关闭", command=win.destroy).pack(side=tk.RIGHT)

    def _show_gpu_layers_help(self):
        win = self._create_toplevel()
        win.title("GPU层数详细说明")
        win.geometry("650x580")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 650) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 580) // 2
        win.geometry(f"+{x}+{y}")

        main_frame = ttk.Frame(win, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(main_frame, text="🎮 GPU层数详细说明", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))

        text_widget = tk.Text(main_frame, font=("Microsoft YaHei", 10), wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10, bg="#f5f5f5", spacing1=2, spacing3=2)
        text_widget.pack(fill=tk.BOTH, expand=True)

        text_widget.insert("1.0", "GPU层决定了有多少模型层加载到显卡显存中，\n剩余的层会在CPU内存中运行。\n\n")

        text_widget.insert(tk.END, "━━━━━━━━━━━━━━ 推荐设置 ━━━━━━━━━━━━━━\n\n")
        text_widget.insert(tk.END, "  60（默认）  → 适合大多数显卡，平衡性能和显存\n", "recommend")
        text_widget.insert(tk.END, "  80          → 显存充足时推荐，速度更快\n")
        text_widget.insert(tk.END, "  999         → 全部放显卡，需要足够显存\n")
        text_widget.insert(tk.END, "  30~40       → 显存紧张时使用\n")
        text_widget.insert(tk.END, "  0           → 全部放CPU，最慢，不推荐\n\n")

        text_widget.insert(tk.END, "━━━━━━━━━━━━━━ CPU和GPU叠加计算 ━━━━━━━━━━━━━━\n\n")
        text_widget.insert(tk.END, "  是的！CPU和GPU可以同时工作：\n\n")
        text_widget.insert(tk.END, "  模型总层数: 80层\n")
        text_widget.insert(tk.END, "  GPU层设置: 60层\n")
        text_widget.insert(tk.END, "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        text_widget.insert(tk.END, "  GPU 处理: [第1层] → [第2层] → ... → [第60层]  ← 快\n")
        text_widget.insert(tk.END, "  CPU 处理: [第61层] → ... → [第80层]           ← 慢\n")
        text_widget.insert(tk.END, "  数据流向: GPU层 → CPU层 → 输出\n\n")

        text_widget.insert(tk.END, "━━━━━━━━━━━━━━ 速度对比 ━━━━━━━━━━━━━━\n\n")
        text_widget.insert(tk.END, "  GPU层=999 (全部GPU)  → 约30 tok/s  最快\n")
        text_widget.insert(tk.END, "  GPU层=80  (大部分GPU) → 约25 tok/s  快\n")
        text_widget.insert(tk.END, "  GPU层=60  (默认)     → 约20 tok/s  推荐\n")
        text_widget.insert(tk.END, "  GPU层=30  (小部分GPU) → 约10 tok/s  较慢\n")
        text_widget.insert(tk.END, "  GPU层=0   (全部CPU)  → 约3 tok/s   最慢\n\n")

        text_widget.insert(tk.END, "━━━━━━━━━━━━━━ 实际场景 ━━━━━━━━━━━━━━\n\n")
        text_widget.insert(tk.END, "  场景1: 显存充足 (12GB显存，模型8GB)\n")
        text_widget.insert(tk.END, "    → 设置 GPU层=999，全部放GPU，速度最快\n\n")
        text_widget.insert(tk.END, "  场景2: 显存不足 (8GB显存，模型10GB)\n")
        text_widget.insert(tk.END, "    → 设置 GPU层=60，大部分放GPU，少部分放CPU\n")
        text_widget.insert(tk.END, "    → 速度会变慢，但能正常运行\n\n")
        text_widget.insert(tk.END, "  场景3: 显存严重不足 (4GB显存，模型10GB)\n")
        text_widget.insert(tk.END, "    → 设置 GPU层=30，小部分放GPU，大部分放CPU\n")
        text_widget.insert(tk.END, "    → 速度会很慢，但至少能跑\n\n")

        text_widget.insert(tk.END, "━━━━━━━━━━━━━━ 如何判断是否在叠加计算 ━━━━━━━━━━━━━━\n\n")
        text_widget.insert(tk.END, "  如果警告提示里出现：\n")
        text_widget.insert(tk.END, "  ⚠️ llama-server 报告显存不足\n")
        text_widget.insert(tk.END, "  或者GPU显存占用很高但速度很慢，\n")
        text_widget.insert(tk.END, "  就说明部分层正在CPU上运行。\n\n")

        text_widget.insert(tk.END, "━━━━━━━━━━━━━━ 优化建议 ━━━━━━━━━━━━━━\n\n")
        text_widget.insert(tk.END, "  • 想要最快速度 → 增加GPU层，直到显存接近满\n")
        text_widget.insert(tk.END, "  • 显存不够     → 降低GPU层，接受速度下降\n")
        text_widget.insert(tk.END, "  • 想要平衡     → GPU层=模型层数的70-80%\n")
        text_widget.insert(tk.END, "  • 不确定时     → 使用默认值60即可\n")

        text_widget.tag_config("recommend", foreground="#007acc", font=("Microsoft YaHei", 10, "bold"))
        text_widget.config(state=tk.DISABLED)

        ttk.Button(main_frame, text="关闭", command=win.destroy).pack(pady=(10, 0))

    def _show_kv_help(self):
        win = self._create_toplevel()
        win.title("KV 缓存压缩类型说明")
        win.geometry("600x520")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 600) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 520) // 2
        win.geometry(f"+{x}+{y}")

        main_frame = ttk.Frame(win, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(main_frame, text="📦 KV 缓存压缩类型说明", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))

        text_widget = tk.Text(main_frame, font=("Microsoft YaHei", 10), wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10, bg="#f5f5f5", spacing1=2, spacing3=2)
        text_widget.pack(fill=tk.BOTH, expand=True)

        text_widget.insert("1.0", "KV 缓存压缩可以大幅减少显存占用，\n但会略微降低生成质量。\n\n")
        text_widget.insert(tk.END, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")

        kv_types = [
            ("f32", "32位浮点", "最高精度，显存占用最大，不推荐开启压缩时使用"),
            ("f16", "16位浮点", "高精度，显存占用较大，适合追求质量"),
            ("bf16", "Brain Float 16", "精度接近f16，部分硬件加速更好"),
            ("q8_0", "8位量化 ⭐推荐", "平衡精度和显存，质量损失极小，推荐使用"),
            ("q5_1", "5位量化(改进)", "精度介于q4和q8之间，略好于q5_0"),
            ("q5_0", "5位量化", "精度介于q4和q8之间"),
            ("q4_1", "4位量化(改进)", "显存占用小，略好于q4_0"),
            ("q4_0", "4位量化", "显存占用小，精度损失明显"),
            ("iq4_nl", "非线性4位量化", "精度略好于q4_1，需要较新版本支持"),
            ("turbo4", "Turbo 4位压缩", "特殊压缩格式，需新版llama.cpp支持"),
        ]

        for kv_type, name, desc in kv_types:
            if kv_type == "q8_0":
                text_widget.insert(tk.END, f"  {kv_type:10s}  {name}\n")
                text_widget.insert(tk.END, f"               {desc}\n\n", "recommend")
            else:
                text_widget.insert(tk.END, f"  {kv_type:10s}  {name}\n")
                text_widget.insert(tk.END, f"               {desc}\n\n")

        text_widget.tag_config("recommend", foreground="#007acc")
        text_widget.insert(tk.END, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        text_widget.insert(tk.END, "💡 建议: 一般情况下使用 q8_0 即可，\n")
        text_widget.insert(tk.END, "   显存紧张时可尝试 q4_0 或 q4_1")
        text_widget.config(state=tk.DISABLED)

        ttk.Button(main_frame, text="关闭", command=win.destroy).pack(pady=(10, 0))

    def _show_usage_guide(self):
        win = self._create_toplevel()
        win.title("佩丽卡监督 - 使用说明")
        win.geometry("700x650")
        win.resizable(True, True)
        win.transient(None)

        main_frame = ttk.Frame(win, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(main_frame, text="📖 佩丽卡监督 使用说明", font=("Microsoft YaHei", 14, "bold")).pack(anchor="w", pady=(0, 10))

        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        tab1 = ttk.Frame(notebook, padding=10)
        notebook.add(tab1, text="快速开始")
        text1 = tk.Text(tab1, font=("Microsoft YaHei", 10), wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10, bg="#f5f5f5", spacing1=2, spacing3=2)
        text1.pack(fill=tk.BOTH, expand=True)
        text1.insert("1.0", "━━━━━━━━━━━━━━ 快速开始 ━━━━━━━━━━━━━━\n\n")
        text1.insert(tk.END, "第一步：下载 llama.cpp\n")
        text1.insert(tk.END, "  • 访问 https://github.com/ggml-org/llama.cpp/releases\n")
        text1.insert(tk.END, "  • 下载最新版本的 llama-bwin-bin-win-cuda-x64.zip\n")
        text1.insert(tk.END, "  • 解压到任意文件夹\n\n")
        text1.insert(tk.END, "第二步：下载模型\n")
        text1.insert(tk.END, "  • 访问 https://huggingface.co 搜索 GGUF\n")
        text1.insert(tk.END, "  • 或访问 https://modelscope.cn（国内镜像）\n")
        text1.insert(tk.END, "  • 下载 .gguf 格式的模型文件\n\n")
        text1.insert(tk.END, "第三步：配置佩丽卡监督\n")
        text1.insert(tk.END, "  • 点击「浏览」选择 llama-server.exe\n")
        text1.insert(tk.END, "  • 点击「浏览」选择模型文件夹\n")
        text1.insert(tk.END, "  • 在下拉框中选择要使用的模型\n\n")
        text1.insert(tk.END, "第四步：启动服务\n")
        text1.insert(tk.END, "  • 点击「启动服务」按钮\n")
        text1.insert(tk.END, "  • 等待模型加载完成（可能需要几分钟）\n")
        text1.insert(tk.END, "  • 在浏览器中访问 http://127.0.0.1:8080/\n\n")
        text1.insert(tk.END, "第五步：使用AI\n")
        text1.insert(tk.END, "  • 在WebUI中输入问题，开始对话\n")
        text1.insert(tk.END, "  • 或使用第三方工具连接API\n")
        text1.config(state=tk.DISABLED)

        tab2 = ttk.Frame(notebook, padding=10)
        notebook.add(tab2, text="性能参数")
        text2 = tk.Text(tab2, font=("Microsoft YaHei", 10), wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10, bg="#f5f5f5", spacing1=2, spacing3=2)
        text2.pack(fill=tk.BOTH, expand=True)
        text2.insert("1.0", "━━━━━━━━━━━━━━ 性能参数说明 ━━━━━━━━━━━━━━\n\n")
        text2.insert(tk.END, "🎮 GPU 层 (默认: 60)\n")
        text2.insert(tk.END, "  决定有多少模型层加载到显卡显存中\n")
        text2.insert(tk.END, "  • 60 (推荐) → 平衡性能和显存\n")
        text2.insert(tk.END, "  • 80        → 显存充足时更快\n")
        text2.insert(tk.END, "  • 999       → 全部放显卡\n")
        text2.insert(tk.END, "  • 30~40     → 显存紧张时\n")
        text2.insert(tk.END, "  • 0         → 全部放CPU（最慢）\n\n")
        text2.insert(tk.END, "📝 上下文 (默认: 65536)\n")
        text2.insert(tk.END, "  决定AI能记住多少对话内容\n")
        text2.insert(tk.END, "  • 越大 → 记忆越多，但显存占用越多\n")
        text2.insert(tk.END, "  • 推荐: 8192~65536\n")
        text2.insert(tk.END, "  • 显存不够时降低此值\n\n")
        text2.insert(tk.END, "🧵 线程 (默认: 12)\n")
        text2.insert(tk.END, "  CPU处理的线程数\n")
        text2.insert(tk.END, "  • 填你的CPU逻辑处理器数\n")
        text2.insert(tk.END, "  • 6核12线程填12\n")
        text2.insert(tk.END, "  • 点击「?」按钮查看如何查看\n\n")
        text2.insert(tk.END, "📦 批处理 (默认: 4096)\n")
        text2.insert(tk.END, "  一次处理的token数量\n")
        text2.insert(tk.END, "  • 越大 → 处理越快，但显存占用越多\n")
        text2.insert(tk.END, "  • 推荐: 2048~8192\n\n")
        text2.insert(tk.END, "🔌 端口 (默认: 8080)\n")
        text2.insert(tk.END, "  Web服务的端口号\n")
        text2.insert(tk.END, "  • 修改后需要重新启动服务\n")
        text2.insert(tk.END, "  • 确保端口没有被其他程序占用\n\n")
        text2.insert(tk.END, "👥 并行 (默认: 1)\n")
        text2.insert(tk.END, "  同时处理的请求数量\n")
        text2.insert(tk.END, "  • 1 → 单用户使用\n")
        text2.insert(tk.END, "  • 2~4 → 多用户同时使用\n")
        text2.insert(tk.END, "  • 越大 → 显存占用越多\n\n")
        text2.insert(tk.END, "🧠 推理 (默认: off)\n")
        text2.insert(tk.END, "  是否显示AI的思考过程\n")
        text2.insert(tk.END, "  • off → 不显示思考过程\n")
        text2.insert(tk.END, "  • on  → 显示思考过程（需要模型支持）\n\n")
        text2.insert(tk.END, "⚡ Flash Attention (默认: 开启)\n")
        text2.insert(tk.END, "  加速注意力计算，减少显存占用\n")
        text2.insert(tk.END, "  • 推荐开启\n")
        text2.insert(tk.END, "  • 如果遇到问题可以关闭\n\n")
        text2.insert(tk.END, "🗜️ KV 缓存压缩 (默认: 开启)\n")
        text2.insert(tk.END, "  压缩KV缓存，大幅减少显存占用\n")
        text2.insert(tk.END, "  • 推荐开启\n")
        text2.insert(tk.END, "  • 点击「?」查看压缩类型说明\n\n")
        text2.insert(tk.END, "⏱️ 速度保留 (默认: 20秒)\n")
        text2.insert(tk.END, "  生成完成后速度显示保留的时间\n")
        text2.insert(tk.END, "  • 调大可以更久看到上次速度\n")
        text2.config(state=tk.DISABLED)

        tab3 = ttk.Frame(notebook, padding=10)
        notebook.add(tab3, text="第三方工具")
        text3 = tk.Text(tab3, font=("Microsoft YaHei", 10), wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10, bg="#f5f5f5", spacing1=2, spacing3=2)
        text3.pack(fill=tk.BOTH, expand=True)
        text3.insert("1.0", "━━━━━━━━━━━━━━ 第三方工具配置 ━━━━━━━━━━━━━━\n\n")
        text3.insert(tk.END, "佩丽卡监督支持所有兼容OpenAI API的工具。\n\n")
        text3.insert(tk.END, "━━━ 常用工具 ━━━\n\n")
        text3.insert(tk.END, "  • ChatBox (推荐新手)\n")
        text3.insert(tk.END, "  • LobeChat\n")
        text3.insert(tk.END, "  • Open WebUI\n")
        text3.insert(tk.END, "  • Cherry Studio\n")
        text3.insert(tk.END, "  • NextChat\n\n")
        text3.insert(tk.END, "━━━ 配置方法 ━━━\n\n")
        text3.insert(tk.END, "  1. 打开第三方工具\n")
        text3.insert(tk.END, "  2. 找到设置/配置页面\n")
        text3.insert(tk.END, "  3. 选择 OpenAI 兼容 模式\n")
        text3.insert(tk.END, "  4. 填写以下信息:\n\n")
        text3.insert(tk.END, "     API 地址/主机:\n")
        text3.insert(tk.END, "     http://127.0.0.1:8080/v1\n\n")
        text3.insert(tk.END, "     API 密钥:\n")
        text3.insert(tk.END, "     sk-任意字符（随意填写）\n\n")
        text3.insert(tk.END, "     模型名称:\n")
        text3.insert(tk.END, "     可以不填或随意填写\n\n")
        text3.insert(tk.END, "━━━ 局域网访问 ━━━\n\n")
        text3.insert(tk.END, "  如果其他电脑要访问，请使用局域网IP:\n")
        text3.insert(tk.END, "  http://192.168.x.x:8080/v1\n\n")
        text3.insert(tk.END, "  IP地址在启动服务后会显示在「WebUI & API 地址」中\n\n")
        text3.insert(tk.END, "━━━ 常见问题 ━━━\n\n")
        text3.insert(tk.END, "  Q: 连接失败怎么办？\n")
        text3.insert(tk.END, "  A: 确保服务已启动，端口正确\n\n")
        text3.insert(tk.END, "  Q: API密钥填什么？\n")
        text3.insert(tk.END, "  A: 随意填写，如 sk-xxx\n\n")
        text3.insert(tk.END, "  Q: 为什么没有模型列表？\n")
        text3.insert(tk.END, "  A: 可以不选模型，直接发送消息\n")
        text3.config(state=tk.DISABLED)

        tab4 = ttk.Frame(notebook, padding=10)
        notebook.add(tab4, text="常见问题")
        text4 = tk.Text(tab4, font=("Microsoft YaHei", 10), wrap=tk.WORD, relief=tk.FLAT, padx=10, pady=10, bg="#f5f5f5", spacing1=2, spacing3=2)
        text4.pack(fill=tk.BOTH, expand=True)
        text4.insert("1.0", "━━━━━━━━━━━━━━ 常见问题解答 ━━━━━━━━━━━━━━\n\n")
        text4.insert(tk.END, "Q: 启动失败怎么办？\n")
        text4.insert(tk.END, "A: 检查以下几点:\n")
        text4.insert(tk.END, "   • llama-server.exe 路径是否正确\n")
        text4.insert(tk.END, "   • 模型文件是否存在\n")
        text4.insert(tk.END, "   • 路径是否包含中文（必须英文）\n")
        text4.insert(tk.END, "   • 端口是否被占用\n\n")
        text4.insert(tk.END, "Q: 速度很慢怎么办？\n")
        text4.insert(tk.END, "A: 尝试以下优化:\n")
        text4.insert(tk.END, "   • 增加GPU层数\n")
        text4.insert(tk.END, "   • 减小上下文大小\n")
        text4.insert(tk.END, "   • 启用Flash Attention\n")
        text4.insert(tk.END, "   • 启用KV压缩\n")
        text4.insert(tk.END, "   • 使用更小的量化版本\n\n")
        text4.insert(tk.END, "Q: 显存不足怎么办？\n")
        text4.insert(tk.END, "A: 尝试以下方法:\n")
        text4.insert(tk.END, "   • 降低GPU层数（如改为40）\n")
        text4.insert(tk.END, "   • 减小上下文大小\n")
        text4.insert(tk.END, "   • 使用更小的量化版本\n")
        text4.insert(tk.END, "   • 启用KV压缩\n\n")
        text4.insert(tk.END, "Q: 模型加载失败怎么办？\n")
        text4.insert(tk.END, "A: 检查以下几点:\n")
        text4.insert(tk.END, "   • 模型文件是否完整下载\n")
        text4.insert(tk.END, "   • 模型格式是否为GGUF\n")
        text4.insert(tk.END, "   • llama.cpp版本是否支持该模型\n\n")
        text4.insert(tk.END, "Q: 如何更新llama.cpp？\n")
        text4.insert(tk.END, "A: 从GitHub下载最新版本，\n")
        text4.insert(tk.END, "   替换llama-server.exe即可\n\n")
        text4.insert(tk.END, "Q: 路径包含中文怎么办？\n")
        text4.insert(tk.END, "A: llama-server不支持中文路径，\n")
        text4.insert(tk.END, "   请将文件夹重命名为英文\n")
        text4.insert(tk.END, "   例如: D:\\AI\\models\\ 而不是 D:\\AI\\模型\\\n\n")
        text4.insert(tk.END, "Q: 如何查看CPU线程数？\n")
        text4.insert(tk.END, "A: 点击线程旁边的「?」按钮查看\n")
        text4.config(state=tk.DISABLED)

        ttk.Button(main_frame, text="关闭", command=win.destroy).pack(pady=(10, 0))

    # ════════════════════════════════════════════════════════════════
    #  WebUI地址显示
    # ════════════════════════════════════════════════════════════════

    def _update_webui_display(self, port):
        ips = _get_local_ips()
        lines = []
        lines.append("━━━━━━━━━━━━━ WebUI 浏览器访问 ━━━━━━━━━━━━━")
        lines.append("")
        for ip in ips:
            url = f"http://{ip}:{port}/"
            if ip in ["127.0.0.1", "localhost"]:
                lines.append(f"  本地访问:   {url}")
            else:
                lines.append(f"  局域网访问: {url}")
        lines.append("")
        lines.append("━━━━━━━━━━ OpenAI 兼容 API 地址 ━━━━━━━━━━")
        lines.append("")
        lines.append("  用于第三方工具（ChatBox、LobeChat、Cherry Studio 等）")
        lines.append("")
        for ip in ips:
            api_url = f"http://{ip}:{port}/v1"
            if ip in ["127.0.0.1", "localhost"]:
                lines.append(f"  API 主机 (本地):   {api_url}")
            else:
                lines.append(f"  API 主机 (远程):   {api_url}")
        lines.append("")
        lines.append(f"  API 密钥:   随意填写（如 sk-xxx）")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("  配置示例:")
        lines.append("  • ChatBox / LobeChat / Open WebUI:")
        lines.append(f"      API地址 → http://你的IP:{port}/v1")
        lines.append("      API密钥 → sk-任意字符")
        lines.append("  • Cherry Studio / NextChat:")
        lines.append(f"      接口地址 → http://你的IP:{port}/v1")
        lines.append("      密钥 → 随意填写")

        text = "\n".join(lines)

        def _do():
            self.webui_text.config(state=tk.NORMAL)
            self.webui_text.delete("1.0", tk.END)
            self.webui_text.insert("1.0", text)
            for ip in ips:
                url = f"http://{ip}:{port}/"
                start = "1.0"
                while True:
                    pos = self.webui_text.search(url, start, tk.END)
                    if not pos:
                        break
                    end = f"{pos}+{len(url)}c"
                    self.webui_text.tag_add("url", pos, end)
                    start = end
                api_url = f"http://{ip}:{port}/v1"
                start = "1.0"
                while True:
                    pos = self.webui_text.search(api_url, start, tk.END)
                    if not pos:
                        break
                    end = f"{pos}+{len(api_url)}c"
                    self.webui_text.tag_add("api_url", pos, end)
                    start = end
            self.webui_text.config(state=tk.DISABLED)
        self._ui_call(_do)

    def _clear_webui_display(self):
        def _do():
            self.webui_text.config(state=tk.NORMAL)
            self.webui_text.delete("1.0", tk.END)
            self.webui_text.insert("1.0", "  服务未启动，请先点击「启动服务」")
            self.webui_text.config(state=tk.DISABLED)
        self._ui_call(_do)

    # ════════════════════════════════════════════════════════════════
    #  UI 界面
    # ════════════════════════════════════════════════════════════════

    def setup_ui(self):
        self.root = tk.Tk()
        self.root.title("佩丽卡监督 - LLM Launcher")
        self.root.geometry("960x1060")
        self.root.minsize(760, 760)
        self.root.resizable(True, True)

        icon_path = self._find_icon()
        if icon_path:
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        style = ttk.Style()
        style.configure("Big.TButton", font=("Microsoft YaHei", 12, "bold"), padding=(10, 6))
        style.configure("Help.TButton", font=("Microsoft YaHei", 8))
        style.configure("Reset.TButton", font=("Microsoft YaHei", 9), padding=(8, 4))

        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        # 上部配置（左右分栏）
        top_frame = ttk.Frame(main)
        top_frame.pack(fill=tk.X, pady=(0, 6))
        top_frame.columnconfigure(0, weight=1)
        top_frame.columnconfigure(1, weight=1)

        # 左栏：服务器 + 模型
        left_cfg = ttk.Frame(top_frame)
        left_cfg.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        # 服务器路径
        self.srv_frame = ttk.LabelFrame(left_cfg, text=" llama-server.exe 路径 ", padding=5)
        self.srv_frame.pack(fill=tk.X, pady=(0, 5))
        srv_row = ttk.Frame(self.srv_frame)
        srv_row.pack(fill=tk.X)
        self.server_var = tk.StringVar(value=self.config["server_path"])
        ttk.Entry(srv_row, textvariable=self.server_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.srv_browse_btn = ttk.Button(srv_row, text="浏览", width=6, command=self.browse_server)
        self.srv_browse_btn.pack(side=tk.RIGHT, padx=(5, 0))

        # 模型文件夹
        self.dir_frame = ttk.LabelFrame(left_cfg, text=" 模型文件夹 ", padding=5)
        self.dir_frame.pack(fill=tk.X, pady=(0, 5))
        dir_row = ttk.Frame(self.dir_frame)
        dir_row.pack(fill=tk.X)
        self.dir_var = tk.StringVar(value=self.config["models_dir"])
        ttk.Entry(dir_row, textvariable=self.dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.dir_browse_btn = ttk.Button(dir_row, text="浏览", width=6, command=self.browse_dir)
        self.dir_browse_btn.pack(side=tk.RIGHT, padx=(5, 0))

        # 主模型
        model_frame = ttk.LabelFrame(left_cfg, text=" 主模型 (语言模型) ", padding=5)
        model_frame.pack(fill=tk.X, pady=(0, 5))
        drop_row = ttk.Frame(model_frame)
        drop_row.pack(fill=tk.X, pady=(0, 3))
        self.model_var = tk.StringVar()
        self.combo = ttk.Combobox(drop_row, textvariable=self.model_var, font=("Microsoft YaHei", 10), state="readonly")
        self.combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.combo.bind("<<ComboboxSelected>>", self.on_model_select)
        self.refresh_btn = ttk.Button(drop_row, text="🔄", width=3, command=self.load_models)
        self.refresh_btn.pack(side=tk.RIGHT)
        path_row = ttk.Frame(model_frame)
        path_row.pack(fill=tk.X)
        self.model_path_var = tk.StringVar(value=self.config["model_path"])
        ttk.Entry(path_row, textvariable=self.model_path_var, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.single_model_btn = ttk.Button(path_row, text="单独选", width=7, command=self.browse_single_model)
        self.single_model_btn.pack(side=tk.RIGHT)

        # 视觉适配器
        mmproj_frame = ttk.LabelFrame(left_cfg, text=" 视觉适配器 (mmproj, 可选) ", padding=5)
        mmproj_frame.pack(fill=tk.X, pady=(0, 5))
        mmproj_drop = ttk.Frame(mmproj_frame)
        mmproj_drop.pack(fill=tk.X, pady=(0, 3))
        self.mmproj_var = tk.StringVar()
        self.mmproj_combo = ttk.Combobox(mmproj_drop, textvariable=self.mmproj_var, font=("Microsoft YaHei", 10), state="readonly")
        self.mmproj_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.mmproj_combo.bind("<<ComboboxSelected>>", self.on_mmproj_select)
        mmproj_row = ttk.Frame(mmproj_frame)
        mmproj_row.pack(fill=tk.X)
        self.mmproj_path_var = tk.StringVar(value=self.config.get("mmproj_path", ""))
        ttk.Entry(mmproj_row, textvariable=self.mmproj_path_var, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.mmproj_clear_btn = ttk.Button(mmproj_row, text="清除", width=5, command=self.clear_mmproj)
        self.mmproj_clear_btn.pack(side=tk.RIGHT)

        # 右栏：性能参数
        right_cfg = ttk.Frame(top_frame)
        right_cfg.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        params = ttk.LabelFrame(right_cfg, text=" 性能参数 ", padding=10)
        params.pack(fill=tk.BOTH, expand=True)

        # GPU层
        r1 = ttk.Frame(params)
        r1.pack(fill=tk.X, pady=4)
        ttk.Label(r1, text="GPU 层:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.gpu_var = tk.IntVar(value=self.config["gpu_layers"])
        self.gpu_spinbox = ttk.Spinbox(r1, from_=0, to=999, width=6, textvariable=self.gpu_var, font=("Microsoft YaHei", 10), command=self._update_vram_estimate)
        self.gpu_spinbox.pack(side=tk.LEFT, padx=(4, 4))
        self.gpu_help_btn = ttk.Button(r1, text="?", width=2, style="Help.TButton", command=self._show_gpu_layers_help)
        self.gpu_help_btn.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(r1, text="60=推荐，999=全部放显卡", font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT)

        # 上下文
        r2 = ttk.Frame(params)
        r2.pack(fill=tk.X, pady=4)
        ttk.Label(r2, text="上 下 文:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.ctx_var = tk.IntVar(value=self.config["context_size"])
        self.ctx_spinbox = ttk.Spinbox(r2, from_=2048, to=131072, increment=4096, width=8, textvariable=self.ctx_var, font=("Microsoft YaHei", 10), command=self._update_vram_estimate)
        self.ctx_spinbox.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Label(r2, text="最大 token 数，越大显存越多", font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT)

        # 线程
        r3 = ttk.Frame(params)
        r3.pack(fill=tk.X, pady=4)
        ttk.Label(r3, text="线    程:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.thread_var = tk.IntVar(value=self.config["threads"])
        self.thread_spinbox = ttk.Spinbox(r3, from_=1, to=128, width=6, textvariable=self.thread_var, font=("Microsoft YaHei", 10))
        self.thread_spinbox.pack(side=tk.LEFT, padx=(4, 4))
        self.thread_help_btn = ttk.Button(r3, text="?", width=2, style="Help.TButton", command=self._show_thread_help)
        self.thread_help_btn.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(r3, text="CPU 线程数 (如6核12线程填12)", font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT)

        # 批处理 + 端口 + 并行
        r4 = ttk.Frame(params)
        r4.pack(fill=tk.X, pady=4)
        ttk.Label(r4, text="批 处 理:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.batch_var = tk.IntVar(value=self.config["batch_size"])
        self.batch_spinbox = ttk.Spinbox(r4, from_=512, to=8192, increment=512, width=7, textvariable=self.batch_var, font=("Microsoft YaHei", 10), command=self._update_vram_estimate)
        self.batch_spinbox.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Label(r4, text="(一次处理量)", font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(r4, text="端口:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.port_var = tk.IntVar(value=self.config["port"])
        self.port_spinbox = ttk.Spinbox(r4, from_=1024, to=65535, width=6, textvariable=self.port_var, font=("Microsoft YaHei", 10))
        self.port_spinbox.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Label(r4, text="(默认8080)", font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(r4, text="并行:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.parallel_var = tk.IntVar(value=self.config["parallel"])
        self.parallel_spinbox = ttk.Spinbox(r4, from_=1, to=16, width=4, textvariable=self.parallel_var, font=("Microsoft YaHei", 10), command=self._update_vram_estimate)
        self.parallel_spinbox.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Label(r4, text="(1=单用户)", font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT)

        # 推理 + Flash Attention
        r5 = ttk.Frame(params)
        r5.pack(fill=tk.X, pady=4)
        ttk.Label(r5, text="推    理:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.reasoning_var = tk.StringVar(value=self.config["reasoning"])
        self.reasoning_combo = ttk.Combobox(r5, textvariable=self.reasoning_var, values=["off", "on"], width=5, state="readonly", font=("Microsoft YaHei", 10))
        self.reasoning_combo.pack(side=tk.LEFT, padx=(4, 10))
        self.fa_var = tk.BooleanVar(value=self.config["flash_attention"])
        self.fa_check = ttk.Checkbutton(r5, text="Flash Attention (加速+省显存)", variable=self.fa_var)
        self.fa_check.pack(side=tk.LEFT)

        # KV压缩
        r6 = ttk.Frame(params)
        r6.pack(fill=tk.X, pady=4)
        self.kv_var = tk.BooleanVar(value=self.config["kv_compress"])
        self.kv_check = ttk.Checkbutton(r6, text="KV 缓存压缩", variable=self.kv_var, command=self._update_vram_estimate)
        self.kv_check.pack(side=tk.LEFT)
        self.kv_help_btn = ttk.Button(r6, text="?", width=2, style="Help.TButton", command=self._show_kv_help)
        self.kv_help_btn.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(r6, text="类型:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(4, 4))
        self.kv_type_var = tk.StringVar(value=self.config.get("kv_type", "q8_0"))
        self.kv_type_combo = ttk.Combobox(r6, textvariable=self.kv_type_var,
                     values=["q8_0", "q4_0", "q4_1", "q5_0", "q5_1", "f16", "f32", "bf16", "iq4_nl", "turbo4"],
                     width=7, state="readonly", font=("Microsoft YaHei", 10))
        self.kv_type_combo.pack(side=tk.LEFT, padx=(0, 4))
        self.kv_type_combo.bind("<<ComboboxSelected>>", self.on_kv_type_select)

        r6b = ttk.Frame(params)
        r6b.pack(fill=tk.X, pady=(0, 4))
        self.kv_type_desc_var = tk.StringVar(value="")
        ttk.Label(r6b, textvariable=self.kv_type_desc_var, font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT)
        self.on_kv_type_select()

        # 速度保留
        r7 = ttk.Frame(params)
        r7.pack(fill=tk.X, pady=4)
        ttk.Label(r7, text="速度保留:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.speed_time_var = tk.IntVar(value=self.config.get("speed_display_time", 20))
        self.speed_time_spinbox = ttk.Spinbox(r7, from_=1, to=120, width=5, textvariable=self.speed_time_var, font=("Microsoft YaHei", 10))
        self.speed_time_spinbox.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Label(r7, text="秒 (速度显示保留时间)", font=("Microsoft YaHei", 9), foreground="#888").pack(side=tk.LEFT)

        # 按钮行
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(6, 8))
        self.start_btn = ttk.Button(btn_frame, text="▶  启动服务", style="Big.TButton", command=self.start_server)
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.stop_btn = ttk.Button(btn_frame, text="■  停止", style="Big.TButton", command=self.stop_server, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="退出", style="Big.TButton", command=self.on_close).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="使用说明", style="Reset.TButton", command=self._show_usage_guide).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="恢复默认", style="Reset.TButton", command=self.reset_to_default).pack(side=tk.LEFT)

        # 下部状态（左右分栏）
        bottom_frame = ttk.Frame(main)
        bottom_frame.pack(fill=tk.BOTH, expand=True)
        bottom_frame.columnconfigure(0, weight=1)
        bottom_frame.columnconfigure(1, weight=1)
        bottom_frame.rowconfigure(0, weight=1)

        # 左下：生成速度 + 系统资源 + 警告
        left_bottom = ttk.Frame(bottom_frame)
        left_bottom.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        # 生成速度
        speed_frame = ttk.LabelFrame(left_bottom, text=" ⚡ 生成速度 ", padding=8)
        speed_frame.pack(fill=tk.X, pady=(0, 5))
        speed_inner = tk.Frame(speed_frame, bg="#1a1a2e", padx=10, pady=8)
        speed_inner.pack(fill=tk.X)
        self.speed_var = tk.StringVar(value="等待启动...")
        self.speed_label = tk.Label(speed_inner, textvariable=self.speed_var, font=("Consolas", 14, "bold"), fg="#00cc66", bg="#1a1a2e", anchor="w")
        self.speed_label.pack(fill=tk.X)
        self.speed_detail_var = tk.StringVar(value="")
        tk.Label(speed_inner, textvariable=self.speed_detail_var, font=("Consolas", 9), fg="#888888", bg="#1a1a2e", anchor="w").pack(fill=tk.X, pady=(2, 0))
        status_row = tk.Frame(speed_inner, bg="#1a1a2e")
        status_row.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="● 离线")
        self.status_label = tk.Label(status_row, textvariable=self.status_var, font=("Consolas", 9), fg="#666666", bg="#1a1a2e", anchor="w")
        self.status_label.pack(side=tk.LEFT)

        # 系统资源
        res_frame = ttk.LabelFrame(left_bottom, text=" 💻 系统资源 ", padding=8)
        res_frame.pack(fill=tk.X, pady=(0, 5))
        res_inner = tk.Frame(res_frame, bg="#1a1a2e", padx=10, pady=6)
        res_inner.pack(fill=tk.X)

        self.cpu_info_var = tk.StringVar(value="CPU 占用:  检测中...")
        self.cpu_label = tk.Label(res_inner, textvariable=self.cpu_info_var, font=("Consolas", 10), fg="#4ec9b0", bg="#1a1a2e", anchor="w")
        self.cpu_label.pack(fill=tk.X, pady=1)

        self.gpu_info1_var = tk.StringVar(value="GPU 显存:  检测中...")
        self.gpu_label1 = tk.Label(res_inner, textvariable=self.gpu_info1_var, font=("Consolas", 10), fg="#4ec9b0", bg="#1a1a2e", anchor="w")
        self.gpu_label1.pack(fill=tk.X, pady=1)

        self.gpu_info2_var = tk.StringVar(value="")
        self.gpu_label2 = tk.Label(res_inner, textvariable=self.gpu_info2_var, font=("Consolas", 10), fg="#4ec9b0", bg="#1a1a2e", anchor="w")
        self.gpu_label2.pack(fill=tk.X, pady=1)

        self.mem_info_var = tk.StringVar(value="系统内存:  检测中...")
        self.mem_label = tk.Label(res_inner, textvariable=self.mem_info_var, font=("Consolas", 10), fg="#4ec9b0", bg="#1a1a2e", anchor="w")
        self.mem_label.pack(fill=tk.X, pady=1)

        self.proc_info_var = tk.StringVar(value="进程内存:  N/A")
        self.proc_label = tk.Label(res_inner, textvariable=self.proc_info_var, font=("Consolas", 10), fg="#888888", bg="#1a1a2e", anchor="w")
        self.proc_label.pack(fill=tk.X, pady=1)

        self.start_resource_monitor()

        # 警告提示
        warn_frame = ttk.LabelFrame(left_bottom, text=" ⚠️ 警告提示 ", padding=8)
        warn_frame.pack(fill=tk.BOTH, expand=True)
        self.warn_text = tk.Text(warn_frame, height=6, font=("Microsoft YaHei", 9), bg="#1e1e1e", fg="#666666", wrap=tk.WORD, relief=tk.FLAT, padx=6, pady=4)
        self.warn_text.pack(fill=tk.BOTH, expand=True)
        self.warn_text.insert("1.0", "系统资源正常，等待启动服务")
        self.warn_text.config(state=tk.DISABLED)

        # 右下：显存预估 + WebUI + 日志
        right_bottom = ttk.Frame(bottom_frame)
        right_bottom.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        # ════════════════════════════════════════════════════════════════
        #  📐 显存预估 黑底方框（启动前实时预估）
        # ════════════════════════════════════════════════════════════════
        vram_frame = tk.Frame(right_bottom, bg="#1a1a2e", padx=10, pady=8)
        vram_frame.pack(fill=tk.X, pady=(0, 5))

        tk.Label(vram_frame, text="📐 显存预估（根据模型大小和参数实时计算）",
                 font=("Microsoft YaHei", 10, "bold"), fg="#ffcc00", bg="#1a1a2e").pack(anchor="w")

        self.vram_model_var = tk.StringVar(value="  模型权重:  N/A")
        self.vram_kv_var = tk.StringVar(value="  KV 缓存:   N/A")
        self.vram_buf_var = tk.StringVar(value="  计算缓冲:  N/A")
        self.vram_total_var = tk.StringVar(value="合计: N/A")
        self.vram_fit_var = tk.StringVar(value="请先选择模型文件")

        tk.Label(vram_frame, textvariable=self.vram_model_var,
                 font=("Consolas", 10), fg="#4ec9b0", bg="#1a1a2e", anchor="w").pack(fill=tk.X)
        tk.Label(vram_frame, textvariable=self.vram_kv_var,
                 font=("Consolas", 10), fg="#4ec9b0", bg="#1a1a2e", anchor="w").pack(fill=tk.X)
        tk.Label(vram_frame, textvariable=self.vram_buf_var,
                 font=("Consolas", 10), fg="#4ec9b0", bg="#1a1a2e", anchor="w").pack(fill=tk.X)

        sep = tk.Frame(vram_frame, height=1, bg="#444444")
        sep.pack(fill=tk.X, padx=20, pady=4)

        self.vram_total_label = tk.Label(vram_frame, textvariable=self.vram_total_var,
                 font=("Consolas", 11, "bold"), fg="#4ec9b0", bg="#1a1a2e", anchor="w")
        self.vram_total_label.pack(fill=tk.X)

        tk.Label(vram_frame, textvariable=self.vram_fit_var,
                 font=("Microsoft YaHei", 9), fg="#888888", bg="#1a1a2e", anchor="w").pack(fill=tk.X, pady=(2, 0))
        # ════════════════════════════════════════════════════════════════

        webui_frame = ttk.LabelFrame(right_bottom, text=" 🌐 WebUI & API 地址 ", padding=8)
        webui_frame.pack(fill=tk.X, pady=(0, 5))
        self.webui_text = tk.Text(webui_frame, height=10, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=4)
        self.webui_text.pack(fill=tk.BOTH, expand=True)
        self.webui_text.insert("1.0", "  服务未启动，请先点击「启动服务」")
        self.webui_text.tag_config("url", foreground="#4ec9b0", underline=True)
        self.webui_text.tag_config("api_url", foreground="#ffcc00", underline=True)
        self.webui_text.config(state=tk.DISABLED)

        log_frame = ttk.LabelFrame(right_bottom, text=" 📋 运行日志 ", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_area = scrolledtext.ScrolledText(log_frame, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4")
        self.log_area.pack(fill=tk.BOTH, expand=True)
        self.log_area.tag_config("info", foreground="#4ec9b0")
        self.log_area.tag_config("error", foreground="#f44747")
        self.log_area.tag_config("warn", foreground="#ce9178")
        self.log_area.tag_config("success", foreground="#6a9955")
        self.log_area.tag_config("speed", foreground="#00ff66")
        self.log_area.tag_config("dim", foreground="#555555")

    # ════════════════════════════════════════════════════════════════
    #  警告提示管理
    # ════════════════════════════════════════════════════════════════

    def _show_warning(self, message, level="warn"):
        def _do():
            self.warn_text.config(state=tk.NORMAL)
            self.warn_text.delete("1.0", tk.END)
            self.warn_text.insert("1.0", message)
            if level == "error":
                self.warn_text.config(fg="#f44747")
            elif level == "warn":
                self.warn_text.config(fg="#ff9933")
            elif level == "ok":
                self.warn_text.config(fg="#4ec9b0")
            else:
                self.warn_text.config(fg="#666666")
            self.warn_text.config(state=tk.DISABLED)
        self._ui_call(_do)

    def _clear_warning(self):
        def _do():
            self.warn_text.config(state=tk.NORMAL)
            self.warn_text.delete("1.0", tk.END)
            self.warn_text.insert("1.0", "系统资源正常，等待启动服务")
            self.warn_text.config(fg="#666666")
            self.warn_text.config(state=tk.DISABLED)
        self._ui_call(_do)

    # ════════════════════════════════════════════════════════════════
    #  模型管理
    # ════════════════════════════════════════════════════════════════

    def _is_mmproj(self, filename):
        return os.path.basename(filename).lower().startswith("mmproj")

    def load_models(self):
        folder = self.dir_var.get().strip()
        if not folder or not os.path.isdir(folder):
            self.log_msg(f"文件夹不存在: {folder}", "error")
            self.combo["values"] = []
            self.mmproj_combo["values"] = []
            return
        self.model_files = []
        self.mmproj_files = []
        for f in sorted(Path(folder).rglob("*.gguf")):
            full = str(f)
            try:
                rel = os.path.relpath(full, folder)
            except Exception:
                rel = os.path.basename(full)
            if self._is_mmproj(full):
                self.mmproj_files.append((rel, full))
            else:
                self.model_files.append((rel, full))
        self.combo["values"] = [item[0] for item in self.model_files]
        current_model = self.model_path_var.get()
        if current_model:
            for i, (_, path) in enumerate(self.model_files):
                if path == current_model:
                    self.combo.current(i)
                    break
        mmproj_display = ["（不使用视觉适配器）"] + [item[0] for item in self.mmproj_files]
        self.mmproj_combo["values"] = mmproj_display
        current_mmproj = self.mmproj_path_var.get()
        if current_mmproj:
            for i, (_, path) in enumerate(self.mmproj_files):
                if path == current_mmproj:
                    self.mmproj_combo.current(i + 1)
                    break
            else:
                self.mmproj_combo.current(0)
        else:
            self.mmproj_combo.current(0)
        self.log_msg(f"已加载 {len(self.model_files)} 个模型, {len(self.mmproj_files)} 个视觉适配器", "info")
        self._update_vram_estimate()

    def on_model_select(self, event=None):
        idx = self.combo.current()
        if 0 <= idx < len(self.model_files):
            name, path = self.model_files[idx]
            self.model_path_var.set(path)
            self.log_msg(f"选择模型: {name}", "info")
            self._update_vram_estimate()

    def on_mmproj_select(self, event=None):
        idx = self.mmproj_combo.current()
        if idx <= 0:
            self.mmproj_path_var.set("")
            self.log_msg("已清除视觉适配器", "info")
        else:
            mmproj_idx = idx - 1
            if 0 <= mmproj_idx < len(self.mmproj_files):
                name, path = self.mmproj_files[mmproj_idx]
                self.mmproj_path_var.set(path)
                self.log_msg(f"选择视觉适配器: {name}", "info")

    def clear_mmproj(self):
        self.mmproj_path_var.set("")
        self.mmproj_combo.current(0)
        self.log_msg("已清除视觉适配器", "info")

    def browse_dir(self):
        path = filedialog.askdirectory(title="选择模型文件夹")
        if path:
            has_chinese, error_msg = _check_path_for_chinese(path)
            if has_chinese:
                messagebox.showerror("路径错误", error_msg)
                return
            self.dir_var.set(path)
            self.load_models()

    def browse_server(self):
        path = filedialog.askopenfilename(title="选择 llama-server.exe", filetypes=[("EXE", "*.exe")])
        if path:
            has_chinese, error_msg = _check_path_for_chinese(path)
            if has_chinese:
                messagebox.showerror("路径错误", error_msg)
                return
            self.server_var.set(path)

    def browse_single_model(self):
        path = filedialog.askopenfilename(title="选择模型", filetypes=[("GGUF", "*.gguf")])
        if path:
            has_chinese, error_msg = _check_path_for_chinese(path)
            if has_chinese:
                messagebox.showerror("路径错误", error_msg)
                return
            if self._is_mmproj(path):
                self.mmproj_path_var.set(path)
                self.log_msg(f"手动选择视觉适配器: {os.path.basename(path)}", "info")
            else:
                self.model_path_var.set(path)
                self.combo.set(os.path.basename(path))
                self.log_msg(f"手动选择: {os.path.basename(path)}", "info")
                self._update_vram_estimate()

    # ════════════════════════════════════════════════════════════════
    #  UI 更新
    # ════════════════════════════════════════════════════════════════

    def _ui_call(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _show_generating(self):
        def _do():
            dots = "." * (self._gen_dot_count % 4)
            self.speed_var.set(f"⏳ 生成中{dots}")
            self.speed_label.config(fg="#ffcc00")
            self.status_var.set("● 生成中")
            self.status_label.config(fg="#00ff66")
            self._gen_dot_count += 1
        self._ui_call(_do)

    def _show_speed_result(self, gen_speed, prompt_speed, tokens):
        def _do():
            self._generating = False
            parts = []
            if gen_speed and gen_speed > 0:
                parts.append(f"⚡ 生成: {gen_speed:.1f} tok/s")
            if prompt_speed and prompt_speed > 0:
                parts.append(f"📥 Prompt: {prompt_speed:.1f} tok/s")
            if tokens and tokens > 0:
                parts.append(f"📊 {tokens} tokens")
            if parts:
                self.speed_var.set("  |  ".join(parts))
                if gen_speed and gen_speed >= 30:
                    self.speed_label.config(fg="#00ff66")
                elif gen_speed and gen_speed >= 15:
                    self.speed_label.config(fg="#ffcc00")
                elif gen_speed and gen_speed >= 5:
                    self.speed_label.config(fg="#ff9933")
                else:
                    self.speed_label.config(fg="#ff4444")
            details = []
            if self.gen_speed_samples:
                avg = sum(self.gen_speed_samples) / len(self.gen_speed_samples)
                details.append(f"历史均速: {avg:.1f} | 峰值: {max(self.gen_speed_samples):.1f}")
            if self.session_start_time:
                elapsed = (datetime.now() - self.session_start_time).total_seconds()
                details.append(f"运行: {elapsed / 60:.1f}分")
            if self.session_total_tokens > 0:
                details.append(f"总tokens: {self.session_total_tokens}")
            self.speed_detail_var.set("  |  ".join(details) if details else "")
            self.status_var.set("● 在线")
            self.status_label.config(fg="#00ff66")
            display_time = self.speed_time_var.get() * 1000
            self.root.after(display_time, self._show_idle)
        self._ui_call(_do)

    def _show_idle(self):
        if self._generating:
            return
        def _do():
            self.speed_var.set("🟢 在线 (等待请求)")
            self.speed_label.config(fg="#4ec9b0")
            self.status_var.set("● 在线")
            self.status_label.config(fg="#00ff66")
            details = []
            if self.gen_speed_samples:
                avg = sum(self.gen_speed_samples) / len(self.gen_speed_samples)
                details.append(f"历史均速: {avg:.1f} | 峰值: {max(self.gen_speed_samples):.1f}")
            if self.session_start_time:
                elapsed = (datetime.now() - self.session_start_time).total_seconds()
                details.append(f"运行: {elapsed / 60:.1f}分")
            if self.session_total_tokens > 0:
                details.append(f"总tokens: {self.session_total_tokens}")
            self.speed_detail_var.set("  |  ".join(details) if details else "")
        self._ui_call(_do)

    def _show_loading(self, text="启动中..."):
        def _do():
            self.speed_var.set(text)
            self.speed_label.config(fg="#ffcc00")
            self.speed_detail_var.set("")
            self.status_var.set("● 启动中")
            self.status_label.config(fg="#ffcc00")
        self._ui_call(_do)

    def _show_stopped(self):
        def _do():
            if self.gen_speed_samples:
                last = self.gen_speed_samples[-1]
                self.speed_var.set(f"已停止 (上次: {last:.1f} tok/s)")
                self.speed_label.config(fg="#666666")
            else:
                self.speed_var.set("已停止")
                self.speed_label.config(fg="#666666")
            self.speed_detail_var.set("")
            self.status_var.set("● 离线")
            self.status_label.config(fg="#666666")
        self._ui_call(_do)

    def _start_generating_animation(self):
        self._generating = True
        self._gen_dot_count = 0
        self._show_generating()
        def animate():
            if self._generating:
                self._show_generating()
                self.root.after(300, animate)
        self.root.after(300, animate)

    # ════════════════════════════════════════════════════════════════
    #  显存不足警告
    # ════════════════════════════════════════════════════════════════

    def _show_gpu_warning(self):
        self._gpu_warning_shown = True

    # ════════════════════════════════════════════════════════════════
    #  日志
    # ════════════════════════════════════════════════════════════════

    _FILTER_PATTERNS = [
        re.compile(r'all\s+slots\s+are\s+idle', re.IGNORECASE),
        re.compile(r'GET\s+/slots\b', re.IGNORECASE),
        re.compile(r'GET\s+/tools\b', re.IGNORECASE),
        re.compile(r'GET\s+/health\b', re.IGNORECASE),
        re.compile(r'common_memory_breakdown', re.IGNORECASE),
        re.compile(r'common_params_fit', re.IGNORECASE),
    ]

    def _should_filter_log(self, line):
        for pat in self._FILTER_PATTERNS:
            if pat.search(line):
                return True
        return False

    def log_msg(self, msg, tag=""):
        self.log_area.insert(tk.END, msg + "\n", tag)
        self.log_area.see(tk.END)

    def log_line_threadsafe(self, msg, tag=""):
        try:
            self.root.after(0, lambda m=msg, t=tag: self.log_msg(m, t))
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════
    #  stdout 解析
    # ════════════════════════════════════════════════════════════════

    def _parse_stdout_line(self, line):
        lower = line.lower().strip()
        if "prompt processing done" in lower:
            return ("gen_start", None)
        m = re.search(
            r'prompt\s+eval\s+time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens.*?'
            r'([\d.]+)\s*tokens\s+per\s+second', lower)
        if m:
            return ("prompt_speed", {"ms": float(m.group(1)), "tokens": int(m.group(2)), "tps": float(m.group(3))})
        if "prompt eval" not in lower:
            m = re.search(
                r'eval\s+time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens.*?'
                r'([\d.]+)\s*tokens\s+per\s+second', lower)
            if m:
                return ("gen_speed", {"ms": float(m.group(1)), "tokens": int(m.group(2)), "tps": float(m.group(3))})
        m = re.search(r'total\s+time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens', lower)
        if m:
            return ("total", {"ms": float(m.group(1)), "tokens": int(m.group(2))})
        if "slot" in lower and "release" in lower and "stop processing" in lower:
            m2 = re.search(r'n_tokens\s*=\s*(\d+)', lower)
            return ("gen_end", {"n_tokens": int(m2.group(1))} if m2 else {})
        if "failed to fit params to free device memory" in lower:
            return ("gpu_warning", None)
        return None

    # ════════════════════════════════════════════════════════════════
    #  启动服务器
    # ════════════════════════════════════════════════════════════════

    def start_server(self):
        server = self.server_var.get().strip()
        model = self.model_path_var.get().strip()
        if not server or not os.path.isfile(server):
            messagebox.showerror("错误", "找不到 llama-server.exe，请先选择服务器程序")
            return
        if not model or not os.path.isfile(model):
            messagebox.showerror("错误", "请先选择主模型文件")
            return
        if self._is_mmproj(model):
            messagebox.showerror("错误", "你选择的是 mmproj 视觉适配器，不是主模型！\n\n请在「主模型」中选择语言模型")
            return
        has_chinese_server, error_server = _check_path_for_chinese(server)
        if has_chinese_server:
            messagebox.showerror("路径错误", f"服务器路径包含中文字符：\n\n{error_server}")
            return
        has_chinese_model, error_model = _check_path_for_chinese(model)
        if has_chinese_model:
            messagebox.showerror("路径错误", f"模型路径包含中文字符：\n\n{error_model}")
            return
        mmproj = self.mmproj_path_var.get().strip()
        if mmproj and os.path.isfile(mmproj):
            has_chinese_mmproj, error_mmproj = _check_path_for_chinese(mmproj)
            if has_chinese_mmproj:
                messagebox.showerror("路径错误", f"视觉适配器路径包含中文字符：\n\n{error_mmproj}")
                return

        self.save_config()
        self._stopping = False
        self._generating = False
        self._gpu_warning_shown = False
        self._is_running = True
        self.session_start_time = datetime.now()
        self.gen_speed_samples = []
        self.prompt_speed_samples = []
        self.session_total_tokens = 0

        self.log_area.delete("1.0", tk.END)
        self._clear_warning()
        self.log_msg("正在启动服务器...", "info")
        self.log_msg(f"主模型: {os.path.basename(model)}", "info")

        if mmproj and os.path.isfile(mmproj):
            self.log_msg(f"视觉适配器: {os.path.basename(mmproj)}", "info")
        else:
            mmproj = ""

        port = self.port_var.get()
        parallel = self.parallel_var.get()
        self.log_msg(f"参数: GPU层={self.gpu_var.get()} 上下文={self.ctx_var.get()} 并行={parallel} 端口={port}", "info")
        self._update_webui_display(port)

        self._set_controls_state("disabled")

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._show_loading("启动中...")

        cmd = [
            server, "-m", model,
            "-ngl", str(self.gpu_var.get()),
            "-c", str(self.ctx_var.get()),
            "-b", str(self.batch_var.get()),
            "-ub", "512",
            "-t", str(self.thread_var.get()),
            "-np", str(parallel),
            "--host", "0.0.0.0", "--port", str(port),
            "--jinja",
            "--reasoning", self.reasoning_var.get(),
            "--log-timestamps", "--metrics",
        ]

        if mmproj:
            cmd.extend(["--mmproj", mmproj])
            self.log_msg(f"已加载视觉适配器: {os.path.basename(mmproj)}", "success")
        if self.fa_var.get():
            cmd.extend(["-fa", "on"])
        if self.kv_var.get():
            kv_type = self.kv_type_var.get()
            cmd.extend(["-ctk", kv_type, "-ctv", kv_type])

        self.start_health_polling()

        pending_prompt_speed = None
        pending_gen_speed = None
        pending_total = None

        def run():
            nonlocal cmd, pending_prompt_speed, pending_gen_speed, pending_total
            try:
                startupinfo = None
                create_flags = 0
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                    create_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    startupinfo=startupinfo, creationflags=create_flags,
                )
                self.process_pid = self.process.pid
                self.log_line_threadsafe(f"✓ 进程已创建 (PID: {self.process_pid})", "success")

                for line in self.process.stdout:
                    if self._stopping:
                        break
                    line = line.rstrip()
                    if not line:
                        continue
                    if self._should_filter_log(line):
                        continue

                    lower = line.lower()
                    parsed = self._parse_stdout_line(line)

                    if parsed:
                        ptype, pdata = parsed
                        if ptype == "gen_start":
                            self._start_generating_animation()
                            pending_prompt_speed = None
                            pending_gen_speed = None
                            pending_total = None
                            continue
                        elif ptype == "prompt_speed":
                            pending_prompt_speed = pdata["tps"]
                            self.prompt_speed_samples.append(pdata["tps"])
                            if len(self.prompt_speed_samples) > 200:
                                self.prompt_speed_samples = self.prompt_speed_samples[-200:]
                            self.log_line_threadsafe(f"📥 Prompt: {pdata['tps']:.1f} tok/s ({pdata['tokens']}t, {pdata['ms']:.0f}ms)", "speed")
                            continue
                        elif ptype == "gen_speed":
                            pending_gen_speed = pdata["tps"]
                            self.gen_speed_samples.append(pdata["tps"])
                            if len(self.gen_speed_samples) > 200:
                                self.gen_speed_samples = self.gen_speed_samples[-200:]
                            self.session_total_tokens += pdata["tokens"]
                            self.log_line_threadsafe(f"⚡ 生成: {pdata['tps']:.1f} tok/s ({pdata['tokens']}t, {pdata['ms']:.0f}ms)", "speed")
                            continue
                        elif ptype == "total":
                            pending_total = pdata
                            self.log_line_threadsafe(f"📊 总计: {pdata['tokens']}t, {pdata['ms']:.0f}ms", "speed")
                            continue
                        elif ptype == "gen_end":
                            gen = pending_gen_speed
                            prompt = pending_prompt_speed
                            tokens = pending_total["tokens"] if pending_total else pdata.get("n_tokens", 0)
                            if gen or prompt:
                                self._show_speed_result(gen, prompt, tokens)
                            pending_prompt_speed = None
                            pending_gen_speed = None
                            pending_total = None
                            continue
                        elif ptype == "gpu_warning":
                            if not self._gpu_warning_shown:
                                self._show_gpu_warning()
                                self.log_line_threadsafe("⚠️ 显存不足，已记录警告", "warn")
                            continue

                    if any(x in lower for x in ["error", "fail", "abort", "cannot", "exception"]):
                        tag = "error"
                    elif any(x in lower for x in ["warning", "warn"]):
                        tag = "warn"
                    elif any(x in lower for x in ["listening", "ready", "loaded", "model loaded"]):
                        tag = "success"
                    else:
                        tag = "info"
                    self.log_line_threadsafe(line, tag)

                try:
                    if self.process:
                        self.process.wait()
                except Exception:
                    pass

                try:
                    rc = self.process.returncode if self.process else -1
                except Exception:
                    rc = -1

                if not self._stopping:
                    self.log_line_threadsafe(f"\n服务器已退出 (代码: {rc})", "warn" if rc != 0 else "info")
                if self.session_start_time:
                    elapsed = (datetime.now() - self.session_start_time).total_seconds()
                    self.log_line_threadsafe("=== 会话统计 ===", "info")
                    self.log_line_threadsafe(f"  运行: {elapsed / 60:.1f} 分钟", "info")
                    if self.gen_speed_samples:
                        self.log_line_threadsafe(f"  平均生成速度: {sum(self.gen_speed_samples) / len(self.gen_speed_samples):.1f} tok/s", "info")
                        self.log_line_threadsafe(f"  峰值: {max(self.gen_speed_samples):.1f} tok/s", "info")
                    if self.prompt_speed_samples:
                        self.log_line_threadsafe(f"  平均Prompt速度: {sum(self.prompt_speed_samples) / len(self.prompt_speed_samples):.1f} tok/s", "info")
                    self.log_line_threadsafe(f"  总tokens: {self.session_total_tokens}", "info")
            except Exception as e:
                if not self._stopping:
                    self.log_line_threadsafe(f"错误: {e}", "error")
            finally:
                self.process_pid = None
                self.process = None
                self._generating = False
                if not self._stopping:
                    self.root.after(0, self._on_server_stopped)

        threading.Thread(target=run, daemon=True).start()

    def start_health_polling(self):
        def poll():
            port = self.port_var.get()
            url = f"http://127.0.0.1:{port}/health"
            for _ in range(120):
                if self._stopping:
                    return
                if self.process is None:
                    time.sleep(0.5)
                    continue
                try:
                    if self.process.poll() is not None:
                        return
                except:
                    return
                try:
                    resp = urllib.request.urlopen(url, timeout=3)
                    if resp.status == 200:
                        if not self.server_ready:
                            self.server_ready = True
                            self.log_line_threadsafe("✓ 服务器就绪", "success")
                            self._show_idle()
                        return
                except Exception:
                    pass
                time.sleep(1)
        threading.Thread(target=poll, daemon=True).start()

    def stop_server(self):
        if self._stopping:
            return
        self._stopping = True
        self._generating = False
        self._is_running = False
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)
        self._show_loading("停止中...")
        pid = self.process_pid
        threading.Thread(target=self._do_stop, args=(pid,), daemon=True).start()

    def _do_stop(self, pid):
        if self.process:
            try:
                self.process.kill()
            except Exception:
                pass

        if pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
            except Exception:
                pass

        self.process = None
        self.process_pid = None
        self.server_ready = False

        self.root.after(0, self._on_server_stopped)
        self.root.after(200, lambda: self.log_line_threadsafe("✓ 服务已停止", "success"))

    def _on_server_stopped(self):
        self._is_running = False

        self._set_controls_state("normal")

        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._stopping = False
        self._show_stopped()
        self._clear_webui_display()

    def on_close(self):
        self.save_config()
        self._gpu_monitoring = False
        self._generating = False
        self._stopping = True
        try:
            self.root.destroy()
        except Exception:
            pass
        pid = self.process_pid
        if pid:
            threading.Thread(target=self._cleanup_process, args=(pid,), daemon=True).start()

    def _cleanup_process(self, pid):
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
        except Exception:
            pass
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "llama-server.exe"],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
        except Exception:
            pass

    def on_kv_type_select(self, event=None):
        kv_type = self.kv_type_var.get()
        descriptions = {
            "f32": "32位浮点，最高精度，显存占用最大",
            "f16": "16位浮点，高精度，显存占用较大",
            "bf16": "Brain Float 16，精度接近f16",
            "q8_0": "8位量化，平衡精度和显存（推荐）",
            "q4_0": "4位量化，显存占用小，精度损失明显",
            "q4_1": "4位量化改进版，略好于q4_0",
            "iq4_nl": "非线性4位量化，精度略好于q4_1",
            "q5_0": "5位量化，精度介于q4和q8之间",
            "q5_1": "5位量化改进版，略好于q5_0",
            "turbo4": "特殊压缩格式，需新版llama.cpp支持"
        }
        desc = descriptions.get(kv_type, "")
        self.kv_type_desc_var.set(f"← {desc}")
        self._update_vram_estimate()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = LLMauncher()
    app.run()
