import os
import sys
import subprocess

def check_azure_cli_installed():
    try:
        subprocess.run(["az", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def install_azure_cli():
    print("Azure CLI is not installed. Installing...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "azure-cli"], check=True)
        print("Azure CLI installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install Azure CLI: {e}")
        sys.exit(1)

def patch_azure_cli():
    site_packages = next(p for p in sys.path if 'site-packages' in p)
    file_to_patch = os.path.join(site_packages, 'azure', 'cli', 'core', 'extension', '__init__.py')
    
    with open(file_to_patch, 'r') as f:
        content = f.read()
    
    patched_content = content.replace(
        "from distutils.sysconfig import get_python_lib",
        "import sysconfig\n\ndef get_python_lib():\n    return sysconfig.get_path('purelib')"
    )
    
    with open(file_to_patch, 'w') as f:
        f.write(patched_content)
    
    print("Azure CLI patched successfully.")

if __name__ == "__main__":
    if not check_azure_cli_installed():
        install_azure_cli()
    patch_azure_cli()
