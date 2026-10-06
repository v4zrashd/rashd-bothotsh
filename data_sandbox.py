import os

def safe_join(base_dir: str, *paths: str) -> str:
    """Safely joins paths ensuring that the result is strictly inside base_dir.
    Prevents directory traversal attacks (e.g. using ../..).
    """
    resolved_base = os.path.abspath(base_dir)
    joined = os.path.join(resolved_base, *paths)
    resolved_joined = os.path.abspath(joined)
    
    # Check if the resolved path starts with the base path
    if not resolved_joined.startswith(resolved_base):
        raise PermissionError("Access Denied: Path traversal detected!")
        
    return resolved_joined
