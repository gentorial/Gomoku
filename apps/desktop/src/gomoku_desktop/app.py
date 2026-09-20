import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from gomoku_tools.protocol import Worker


class Gomoku:
    def __init__(self, root):
        self.root = root
        root.title("Gomoku · 桌面对弈")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = None
        self.generation = 0
        self.messages = queue.Queue()
        self.state = None
        self.busy = False
        self.mode = tk.StringVar(value="我执黑")
        self.rule = tk.StringVar(value="freestyle")
        self.cell = 34
        self.margin = 30
        self.size = 15
        self.human = "black"
        self.moves = []

        ttk.Label(root, text="GOMOKU", font=("Georgia", 24)).pack(pady=12)
        settings = ttk.Frame(root)
        settings.pack(padx=16, pady=6, fill=tk.X)
        ttk.Combobox(settings, textvariable=self.mode, values=["我执黑", "我执白", "双人对弈"],
                     state="readonly", width=12).pack(side=tk.LEFT, padx=4)
        ttk.Combobox(settings, textvariable=self.rule, values=["freestyle", "standard"],
                     state="readonly", width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(settings, text="新对局", command=self.start_game).pack(side=tk.LEFT, padx=4)
        self.status = ttk.Label(root, text="正在连接原生引擎")
        self.status.pack(pady=8)
        width = 2 * self.margin + (self.size - 1) * self.cell
        self.canvas = tk.Canvas(root, width=width, height=width, background="#dbc3a0",
                                highlightthickness=0)
        self.canvas.pack(padx=16, pady=8)
        self.canvas.bind("<Button-1>", self.click)
        controls = ttk.Frame(root)
        controls.pack(pady=12)
        self.undo_button = ttk.Button(controls, text="悔棋", command=self.undo)
        self.undo_button.pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="退出", command=self.close).pack(side=tk.LEFT, padx=8)
        self.draw()
        self.start_game()
        root.after(40, self.poll)

    def start_game(self):
        self.generation += 1
        if self.worker:
            self.worker.close()
        self.state = None
        self.moves = []
        self.human = {"我执黑": "black", "我执白": "white", "双人对弈": None}[self.mode.get()]
        self.current_rule = self.rule.get()
        self.draw()
        try:
            self.worker = Worker()
        except OSError as error:
            messagebox.showerror("引擎启动失败", str(error) + "\n请先运行 pnpm engine:build")
            return
        self.submit("inspect", position=self.position())

    def position(self):
        return {"size": self.size, "rule": self.current_rule, "moves": list(self.moves)}

    def submit(self, method, **params):
        self.busy = True
        self.undo_button.configure(state=tk.DISABLED)
        self.status.configure(text="AI 思考中…" if method == "analyze" else "正在更新棋局")
        worker, generation = self.worker, self.generation

        def run():
            try:
                result = worker.request(method, **params)
                if method == "analyze":
                    result = worker.request("play", position=params["position"], move=result["bestMove"])
                self.messages.put((generation, result, None))
            except Exception as error:
                self.messages.put((generation, None, str(error)))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        while not self.messages.empty():
            generation, result, error = self.messages.get_nowait()
            if generation != self.generation:
                continue
            self.busy = False
            self.undo_button.configure(state=tk.NORMAL)
            if error:
                self.status.configure(text="引擎通信错误")
                messagebox.showerror("引擎通信错误", error)
                continue
            self.state = result
            self.moves = result["moves"]
            self.draw()
            if result["status"] != "playing":
                self.status.configure(text={"black_win": "黑棋获胜", "white_win": "白棋获胜",
                                            "draw": "和棋"}[result["status"]])
            elif self.human is not None and result["toMove"] != self.human:
                self.submit("analyze", position=self.position(), limits={"timeMs": 300, "maxDepth": 4})
            else:
                self.status.configure(text="轮到" + ("黑棋" if result["toMove"] == "black" else "白棋"))
        self.root.after(40, self.poll)

    def click(self, event):
        if self.busy or not self.state or self.state["status"] != "playing":
            return
        x = round((event.x - self.margin) / self.cell)
        y = round((event.y - self.margin) / self.cell)
        if not 0 <= x < self.size or not 0 <= y < self.size:
            return
        if {"x": x, "y": y} in self.moves:
            return
        self.submit("play", position=self.position(), move={"x": x, "y": y})

    def undo(self):
        if self.busy or not self.moves:
            return
        moves = self.moves[:-1]
        while self.human is not None and moves and (
                "black" if len(moves) % 2 == 0 else "white") != self.human:
            moves.pop()
        position = self.position()
        position["moves"] = moves
        self.submit("inspect", position=position)

    def draw(self):
        self.canvas.delete("all")
        low, high = self.margin, self.margin + (self.size - 1) * self.cell
        for i in range(self.size):
            coordinate = self.margin + i * self.cell
            self.canvas.create_line(low, coordinate, high, coordinate, fill="#968366")
            self.canvas.create_line(coordinate, low, coordinate, high, fill="#968366")
        for move_index, move in enumerate(self.moves):
            x, y = self.margin + move["x"] * self.cell, self.margin + move["y"] * self.cell
            r = self.cell * 0.42
            black = move_index % 2 == 0
            self.canvas.create_oval(x-r, y-r, x+r, y+r, fill="#202b23" if black else "#fffdf1",
                                    outline="#25352a" if black else "#bbb7a3")
            if move_index == len(self.moves) - 1:
                self.canvas.create_text(x, y, text=str(move_index + 1),
                                        fill="#fffdf1" if black else "#202b23")

    def close(self):
        self.generation += 1
        if self.worker:
            self.worker.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    Gomoku(root)
    root.mainloop()


if __name__ == "__main__":
    main()
