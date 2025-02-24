# Python Project Bundler

## Overview

The Python Project Bundler is a command-line utility designed to bundle a multi-module Python project into a single file. It scans your project directory, detects Python modules, analyzes their dependencies, and produces a single output file with all the code concatenated in the correct dependency order.

This tool is especially useful for:

- Simplifying the deployment of Python applications.
- Packaging projects into a single file for easier distribution.
- Debugging module dependency issues through detailed debug logs.

## Features

- **Automatic Dependency Resolution:**  
  Uses AST parsing to identify local dependencies and performs a topological sort to order the modules correctly.

- **Local Import Removal:**  
  Automatically removes local import statements from the code, since all the code is bundled into one file.

- **Debug Mode:**  
  Enable detailed debugging output with the `-d` command-line option to trace module mapping, dependency extraction, and final assembly.

- **Command-line Interface:**  
  Simple CLI usage:
  ```bash
  python PyBundle.py <project_directory> <entry_file> <output_file> [-d]
For example:
  ```bash
python PyBundle.py myProject "__main__.py" output.py -d
```

Usage
Clone the Repository:

```bash
git clone https://github.com/henceketh/python-project-bundler.git
cd python-module-bundler
```
Run the Bundler:
```bash
python PyBundle.py <project_directory> <entry_file> <output_file> [-d]
```

<project_directory>: The directory containing your Python project.

<entry_file>: The entry point of your application (e.g., __main__.py).

<output_file>: The path for the bundled output file.

-d (optional): Enables debug mode for verbose logging.

Execute the Bundled File:

```bash
python <output_file>
```
