from langchain.tools import tool
from backend.utils.local_python_executor import (
    BASE_BUILTIN_MODULES,
    local_python_executor,
    reset_executor_state,
)


DEFAULT_AUTHORIZED_IMPORTS = [
    'json',
    'pathlib',
    'sqlalchemy',
    'dotenv',
    'os',
    'sys',
    'pandas',
    'rdkit',
    'numpy',
    'matplotlib',
    'rdkit',
    'seaborn',
    'scipy',
    'sklearn',
    'fuzzywuzzy',
    'Bio',
    'posixpath',
    'ntpath',
    'pybel',
    'requests',
    'openpyxl',
    'httpx',
]
authorized_imports = sorted(set(BASE_BUILTIN_MODULES) | set(DEFAULT_AUTHORIZED_IMPORTS))

@tool
def python_executor(code: str):
    """Execute Python code safely with restricted imports.
    
    Variables defined in previous executions are preserved and available in subsequent
    executions, providing persistent state across code blocks within the session.
    
    Args:
        code (str): The code to execute.
        
    Returns:
        The result of the execution.
    """
    
    return local_python_executor(code, authorized_imports)


@tool
def reset_python_state():
    """Reset the Python execution state.
    
    This clears all variables and functions defined in previous executions,
    providing a clean slate for new code execution. Use this when you need
    to start fresh or clear accumulated state.
    
    Returns:
        str: Confirmation message.
    """
    reset_executor_state()
    return "Python execution state has been reset. All previous variables and functions have been cleared."
