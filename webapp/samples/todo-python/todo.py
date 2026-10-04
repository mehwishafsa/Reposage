"""A tiny to-do list you use from the terminal."""

from storage import load_tasks, save_tasks


def add_task(tasks, title):
    """Add a new, not-done task to the list."""
    tasks.append({"title": title, "done": False})
    return tasks


def complete_task(tasks, number):
    """Mark task number `number` (counting from 1) as done."""
    if number < 1 or number > len(tasks):
        print("No task with that number.")
        return tasks
    tasks[number - 1]["done"] = True
    return tasks


def show_tasks(tasks):
    """Print every task with a [x] when it is done."""
    if not tasks:
        print("Nothing to do!")
    for i, task in enumerate(tasks, start=1):
        mark = "x" if task["done"] else " "
        print(f"{i}. [{mark}] {task['title']}")


def main():
    """Read commands until the user types 'quit'."""
    tasks = load_tasks()
    while True:
        command = input("add / done / list / quit: ").strip()
        if command == "add":
            add_task(tasks, input("Task: "))
        elif command == "done":
            complete_task(tasks, int(input("Number: ")))
        elif command == "list":
            show_tasks(tasks)
        elif command == "quit":
            save_tasks(tasks)
            break


if __name__ == "__main__":
    main()
