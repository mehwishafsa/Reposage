"""Save and load the to-do list in a JSON file."""

import json
import os

FILE_NAME = "tasks.json"


def load_tasks():
    """Read the saved tasks, or start with an empty list."""
    if not os.path.exists(FILE_NAME):
        return []
    with open(FILE_NAME) as f:
        return json.load(f)


def save_tasks(tasks):
    """Write all tasks to the file so they are there next time."""
    with open(FILE_NAME, "w") as f:
        json.dump(tasks, f, indent=2)
