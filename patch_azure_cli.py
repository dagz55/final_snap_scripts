import os
import sys

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
    patch_azure_cli()
