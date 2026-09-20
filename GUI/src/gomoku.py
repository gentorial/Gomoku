import queue
import os
import shlex
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class AIEngine:
    """通过 stdin/stdout 与外部五子棋程序通信。"""

    def __init__(self, command, on_message, on_error):
        self.command = command
        self.on_message = on_message
        self.on_error = on_error
        self.process = None
        self.reader_thread = None
        self.messages = queue.Queue()

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        self.stop()
        try:
            # Windows 可直接接收完整命令行；macOS/Linux 按 shell 规则拆分，
            # 但全程不启用 shell，避免命令注入和额外窗口。
            args = self.command if os.name == "nt" else shlex.split(self.command)
            self.process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            self.process = None
            raise RuntimeError(f"无法启动 AI 程序：{exc}") from exc

        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

    def _read_output(self):
        try:
            while self.running:
                line = self.process.stdout.readline()
                if not line:
                    break
                self.messages.put(("message", line.strip()))
        except (OSError, ValueError) as exc:
            self.messages.put(("error", f"读取 AI 输出失败：{exc}"))

        if self.process and self.process.poll() not in (None, 0):
            detail = ""
            try:
                detail = self.process.stderr.read().strip()
            except (OSError, ValueError):
                pass
            text = f"AI 程序异常退出（返回码 {self.process.returncode}）"
            if detail:
                text += f"\n{detail}"
            self.messages.put(("error", text))

    def poll(self):
        while True:
            try:
                kind, text = self.messages.get_nowait()
            except queue.Empty:
                return
            if kind == "message":
                self.on_message(text)
            else:
                self.on_error(text)

    def send(self, command):
        if not self.running or self.process.stdin is None:
            raise RuntimeError("AI 程序尚未运行")
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RuntimeError(f"向 AI 发送命令失败：{exc}") from exc

    def stop(self):
        if not self.process:
            return
        if self.running:
            try:
                self.send("QUIT")
                self.process.wait(timeout=0.5)
            except (RuntimeError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        self.process = None


class Gomoku:
    MODE_PVP = "Human Vs. Human"
    MODE_HUMAN_BLACK = "Human Vs. AI - Black"
    MODE_HUMAN_WHITE = "AI Vs. Human - White"

    def __init__(self, root):
        self.root = root
        self.root.title("五子棋")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.size = 15
        self.cell_size = 40
        self.margin = 30
        self.board = []
        self.current_player = 1
        self.game_over = False
        self.waiting_for_ai = False
        self.ai_player = None
        self.ai_engine = None

        self.mode_var = tk.StringVar(value=self.MODE_PVP)
        self.ai_command_var = tk.StringVar(value="gomoku_ai.exe")

        self.create_widgets()
        self.reset_board()
        self.root.after(50, self.poll_ai)

    def create_widgets(self):
        tk.Label(self.root, text="GOMOKU", font=("Times New Roman", 22, "bold")).pack(
            pady=(12, 4)
        )

        settings = tk.Frame(self.root)
        settings.pack(fill=tk.X, padx=15, pady=4)

        tk.Label(settings, text="Game Mode:", font=("微软雅黑", 11)).grid(
            row=0, column=0, padx=(0, 4), pady=3
        )
        mode_box = ttk.Combobox(
            settings,
            textvariable=self.mode_var,
            values=(self.MODE_PVP, self.MODE_HUMAN_BLACK, self.MODE_HUMAN_WHITE),
            state="readonly",
            width=22,
        )
        mode_box.grid(row=0, column=1, sticky="w", pady=3)

        # tk.Label(settings, text="AI 程序：", font=("微软雅黑", 11)).grid(
        #    row=1, column=0, padx=(0, 4), pady=3
        # )
        # tk.Entry(settings, textvariable=self.ai_command_var, width=40).grid(
        #    row=1, column=1, sticky="we", pady=3
        # )
        # tk.Button(settings, text="浏览…", command=self.choose_ai).grid(
        #    row=1, column=2, padx=5, pady=3
        # )
        # tk.Button(settings, text="开始游戏", command=self.start_game).grid(
        #     row=0, column=2, padx=5, pady=3
        # )

        self.status_label = tk.Label(
            self.root, text="", font=("微软雅黑", 14), fg="#222222"
        )
        self.status_label.pack(pady=4)

        board_width = self.margin * 2 + self.cell_size * (self.size - 1)
        self.canvas = tk.Canvas(
            self.root,
            width=board_width,
            height=board_width,
            bg="#DEB887",
            highlightthickness=0,
        )
        self.canvas.pack(padx=15, pady=8)
        self.canvas.bind("<Button-1>", self.handle_click)

        buttons = tk.Frame(self.root)
        buttons.pack(pady=(4, 12))
        tk.Button(
            buttons, text="Replay", font=("微软雅黑", 11), width=12,
            command=self.start_game,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            buttons, text="Exit", font=("微软雅黑", 11), width=12,
            command=self.close,
        ).pack(side=tk.LEFT, padx=5)

    def choose_ai(self):
        path = filedialog.askopenfilename(title="选择外部 AI 程序")
        if path:
            # 加引号以支持带空格的路径，也允许用户在后面自行添加参数。
            self.ai_command_var.set(f'"{path}"')

    def reset_board(self):
        self.board = [[0 for _ in range(self.size)] for _ in range(self.size)]
        self.current_player = 1
        self.game_over = False
        self.waiting_for_ai = False
        self.draw_board()
        self.update_status()

    def start_game(self):
        if self.ai_engine:
            self.ai_engine.stop()
            self.ai_engine = None

        mode = self.mode_var.get()
        self.ai_player = None
        if mode == self.MODE_HUMAN_BLACK:
            self.ai_player = 2
        elif mode == self.MODE_HUMAN_WHITE:
            self.ai_player = 1

        if self.ai_player is not None:
            command = self.ai_command_var.get().strip()
            if not command:
                messagebox.showerror("无法开始", "请选择或填写外部 AI 程序路径。")
                return
            self.ai_engine = AIEngine(command, self.handle_ai_message, self.handle_ai_error)
            try:
                self.ai_engine.start()
                color = "BLACK" if self.ai_player == 1 else "WHITE"
                self.ai_engine.send(f"START {color}")
            except RuntimeError as exc:
                self.ai_engine = None
                messagebox.showerror("AI 启动失败", str(exc))
                return

        self.reset_board()
        if self.is_ai_turn():
            self.waiting_for_ai = True
            self.update_status()

    def is_ai_turn(self):
        return self.ai_player is not None and self.current_player == self.ai_player

    def handle_click(self, event):
        if self.game_over or self.waiting_for_ai or self.is_ai_turn():
            return

        col = round((event.x - self.margin) / self.cell_size)
        row = round((event.y - self.margin) / self.cell_size)
        if not (0 <= row < self.size and 0 <= col < self.size):
            return
        if not self.make_move(row, col):
            return

        # 人类落子后仅将这一步通知有状态 AI。
        if not self.game_over and self.ai_engine and self.is_ai_turn():
            try:
                self.ai_engine.send(f"MOVE {row} {col}")
                self.waiting_for_ai = True
                self.update_status()
            except RuntimeError as exc:
                self.handle_ai_error(str(exc))

    def make_move(self, row, col):
        """人类和 AI 共用的唯一落子入口。"""
        if self.game_over or not (0 <= row < self.size and 0 <= col < self.size):
            return False
        if self.board[row][col] != 0:
            return False

        player = self.current_player
        self.board[row][col] = player
        self.draw_board()

        if self.check_win(row, col):
            self.finish_game(f"{'黑棋' if player == 1 else '白棋'}获胜！")
            return True
        if self.is_draw():
            self.finish_game("和棋")
            return True

        self.current_player = 2 if player == 1 else 1
        self.update_status()
        return True

    def handle_ai_message(self, line):
        if not line:
            return
        parts = line.split()
        command = parts[0].upper()

        if command == "READY":
            return
        if command == "ERROR":
            self.handle_ai_error("AI 返回错误：" + " ".join(parts[1:]))
            return
        if command != "MOVE" or len(parts) != 3:
            self.handle_ai_error(f"无法识别 AI 输出：{line}")
            return

        try:
            row, col = int(parts[1]), int(parts[2])
        except ValueError:
            self.handle_ai_error(f"AI 坐标不是整数：{line}")
            return

        if not self.waiting_for_ai or not self.is_ai_turn():
            self.handle_ai_error(f"AI 在非 AI 回合返回落子：{line}")
            return

        self.waiting_for_ai = False
        if not self.make_move(row, col):
            self.handle_ai_error(f"AI 返回非法落子：({row}, {col})")

    def handle_ai_error(self, text):
        self.waiting_for_ai = False
        self.game_over = True
        self.status_label.config(text="AI 通信错误")
        messagebox.showerror("AI 通信错误", text)

    def poll_ai(self):
        if self.ai_engine:
            self.ai_engine.poll()
        self.root.after(50, self.poll_ai)

    def finish_game(self, result):
        self.game_over = True
        self.waiting_for_ai = False
        self.status_label.config(text=f"游戏结束：{result}")
        messagebox.showinfo("游戏结束", result)

    def update_status(self):
        if self.game_over:
            return
        color = "黑棋" if self.current_player == 1 else "白棋"
        suffix = "（AI 思考中…）" if self.waiting_for_ai else ""
        self.status_label.config(text=f"当前玩家：{color}{suffix}")

    def draw_board(self):
        self.canvas.delete("all")
        start = self.margin
        end = self.margin + self.cell_size * (self.size - 1)
        for i in range(self.size):
            pos = self.margin + i * self.cell_size
            self.canvas.create_line(start, pos, end, pos, fill="#333333")
            self.canvas.create_line(pos, start, pos, end, fill="#333333")

        for row, col in ((3, 3), (3, 11), (7, 7), (11, 3), (11, 11)):
            x = self.margin + col * self.cell_size
            y = self.margin + row * self.cell_size
            self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#333333", outline="")

        for row in range(self.size):
            for col in range(self.size):
                if self.board[row][col]:
                    self.draw_piece(row, col, self.board[row][col])

    def draw_piece(self, row, col, player):
        x = self.margin + col * self.cell_size
        y = self.margin + row * self.cell_size
        radius = self.cell_size * 0.42
        color, outline = ("#111111", "#000000") if player == 1 else ("#FFFFFF", "#555555")
        self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius,
            fill=color, outline=outline, width=1,
        )

    def check_win(self, row, col):
        player = self.board[row][col]
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            count = 1
            count += self.count_direction(row, col, dr, dc, player)
            count += self.count_direction(row, col, -dr, -dc, player)
            if count >= 5:
                return True
        return False

    def count_direction(self, row, col, dr, dc, player):
        count = 0
        row += dr
        col += dc
        while 0 <= row < self.size and 0 <= col < self.size and self.board[row][col] == player:
            count += 1
            row += dr
            col += dc
        return count

    def is_draw(self):
        return all(cell != 0 for row in self.board for cell in row)

    def close(self):
        if self.ai_engine:
            self.ai_engine.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    Gomoku(root)
    root.mainloop()


if __name__ == "__main__":
    main()
