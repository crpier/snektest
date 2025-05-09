from rich.console import Console

console = Console()
left = "LeftWord"
right = "RightWord"

# Get the width of the console
width = console.size.width

# Calculate the space between the words
space = width - len(left) - len(right)
if space < 1:
    space = 1  # Prevent negative or zero spacing

# Build the line
line = f"{left}{' ' * space}{right}"

console.print(line)
