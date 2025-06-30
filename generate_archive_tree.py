#!/usr/bin/env python3
"""
Standalone script to generate a static HTML file with an expandable tree view
of all files in the 'tva' directory using signed links that expire in 7 days.
"""

import os
import boto3
from botocore.config import Config
from datetime import datetime, timedelta
import json
from collections import defaultdict

# Try to load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Note: python-dotenv not available, using system environment variables")

# DigitalOcean Spaces configuration
s3_client = boto3.client('s3',
    endpoint_url=f'https://{os.getenv("DIGITALOCEAN_SPACES_REGION")}.digitaloceanspaces.com',
    aws_access_key_id=os.getenv("DIGITALOCEAN_SPACES_KEY_ID"),
    aws_secret_access_key=os.getenv("DIGITALOCEAN_SPACES_SECRET"),
    config=Config(signature_version='s3v4')
)

SPACES_BUCKET = os.getenv("DIGITALOCEAN_SPACES_BUCKET")
PDF_BASE_DIR = "tva"  # Base directory in Spaces

def get_all_files_in_tva():
    """Get all files in the tva directory from DigitalOcean Spaces."""
    files = []
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=SPACES_BUCKET, Prefix=f"{PDF_BASE_DIR}/"):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # Skip the base directory itself
                if key == f"{PDF_BASE_DIR}/":
                    continue
                # Skip .code-workspace files
                if key.endswith('.code-workspace'):
                    continue
                files.append({
                    'key': key,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified']
                })
    except Exception as e:
        print(f"Error listing files: {str(e)}")
    return files

def generate_signed_url(key, expiration_days=7):
    """Generate a signed URL for a file that expires in the specified number of days (max 7 for DigitalOcean Spaces)."""
    try:
        # DigitalOcean Spaces has a maximum expiration of 7 days (604800 seconds)
        max_expiration = min(expiration_days * 24 * 60 * 60, 604800)
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': SPACES_BUCKET, 'Key': key},
            ExpiresIn=max_expiration
        )
        return url
    except Exception as e:
        print(f"Error generating signed URL for {key}: {str(e)}")
        return None

def build_tree_structure(files):
    """Build a nested tree structure from the file list."""
    tree = defaultdict(lambda: {'files': [], 'subdirs': defaultdict(lambda: {'files': [], 'subdirs': {}})})
    
    for file_info in files:
        key = file_info['key']
        # Remove the base directory prefix
        relative_path = key[len(f"{PDF_BASE_DIR}/"):]
        path_parts = relative_path.split('/')
        
        if len(path_parts) == 1:
            # File in root of tva
            tree['root']['files'].append(file_info)
        else:
            # File in subdirectory
            current_level = tree['root']['subdirs']
            for i, part in enumerate(path_parts[:-1]):
                if part not in current_level:
                    current_level[part] = {'files': [], 'subdirs': defaultdict(lambda: {'files': [], 'subdirs': {}})}
                current_level = current_level[part]['subdirs']
            
            # Add file to the final directory
            dir_name = path_parts[-2] if len(path_parts) > 1 else 'root'
            parent_level = tree['root']['subdirs']
            for part in path_parts[:-2]:
                parent_level = parent_level[part]['subdirs']
            if path_parts[-2] not in parent_level:
                parent_level[path_parts[-2]] = {'files': [], 'subdirs': {}}
            parent_level[path_parts[-2]]['files'].append(file_info)
    
    return tree

def generate_tree_html(tree_data, level=0):
    """Generate HTML for the tree structure."""
    html = ""
    
    if level == 0:
        # Root level
        if tree_data['root']['files']:
            html += '<ul class="tree-list">\n'
            for file_info in sorted(tree_data['root']['files'], key=lambda x: x['key']):
                filename = os.path.basename(file_info['key'])
                signed_url = generate_signed_url(file_info['key'])
                if signed_url:
                    html += f'    <li class="tree-item file-item"><a href="{signed_url}" target="_blank">{filename}</a></li>\n'
            html += '</ul>\n'
        
        # Subdirectories
        for dir_name in sorted(tree_data['root']['subdirs'].keys()):
            dir_data = tree_data['root']['subdirs'][dir_name]
            html += f'<div class="tree-node">\n'
            html += f'    <div class="tree-toggle" onclick="toggleNode(this)">📁 {dir_name}</div>\n'
            html += f'    <div class="tree-content" style="display: none;">\n'
            html += f'        <ul class="tree-list">\n'
            
            # Files in this directory
            for file_info in sorted(dir_data['files'], key=lambda x: x['key']):
                filename = os.path.basename(file_info['key'])
                signed_url = generate_signed_url(file_info['key'])
                if signed_url:
                    html += f'            <li class="tree-item file-item"><a href="{signed_url}" target="_blank">{filename}</a></li>\n'
            
            html += f'        </ul>\n'
            html += f'    </div>\n'
            html += f'</div>\n'
    
    return html

def generate_html_page():
    """Generate the complete HTML page."""
    created_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    expiry_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    print("Fetching file list from DigitalOcean Spaces...")
    files = get_all_files_in_tva()
    print(f"Found {len(files)} files")
    
    print("Building tree structure...")
    tree_data = build_tree_structure(files)
    
    print("Generating signed URLs and HTML...")
    tree_html = generate_tree_html(tree_data)
    
    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Times-Virginian Archive</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        
        .header {{
            background-color: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        
        .header h1 {{
            margin: 0 0 10px 0;
            color: #333;
            font-size: 2.5em;
        }}
        
        .header p {{
            margin: 5px 0;
            color: #666;
        }}
        
        .archive-container {{
            background-color: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .tree-node {{
            margin: 5px 0;
        }}
        
        .tree-toggle {{
            cursor: pointer;
            padding: 8px;
            background-color: #f0f0f0;
            border: 1px solid #ddd;
            border-radius: 4px;
            user-select: none;
            font-weight: bold;
            color: #333;
        }}
        
        .tree-toggle:hover {{
            background-color: #e0e0e0;
        }}
        
        .tree-content {{
            margin-left: 20px;
            border-left: 2px solid #ddd;
            padding-left: 10px;
        }}
        
        .tree-list {{
            list-style: none;
            padding: 0;
            margin: 10px 0;
        }}
        
        .tree-item {{
            margin: 3px 0;
            padding: 4px 8px;
        }}
        
        .file-item a {{
            color: #000;
            text-decoration: none;
            display: block;
            padding: 4px 8px;
            border-radius: 3px;
        }}
        
        .file-item a:hover {{
            color: #0000FF;
            background-color: #f0f0f0;
        }}
        
        .file-item a:visited {{
            color: #800080;
        }}
        
        .expand-all-btn {{
            background-color: #007bff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            margin-bottom: 20px;
            font-size: 16px;
        }}
        
        .expand-all-btn:hover {{
            background-color: #0056b3;
        }}
        
        .stats {{
            margin-top: 20px;
            padding: 15px;
            background-color: #f8f9fa;
            border-radius: 4px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Times-Virginian Archive</h1>
        <p><strong>The links are pre-signed and point directly to AWS-S3.</strong></p>
        <p><strong>Created:</strong> {created_date}</p>
        <p><strong>Link Expiry:</strong> {expiry_date} (7 days maximum for DigitalOcean Spaces)</p>
    </div>
    
    <div class="archive-container">
        <button class="expand-all-btn" onclick="toggleAll()">Expand All</button>
        
        <div id="tree-container">
            {tree_html}
        </div>
        
        <div class="stats">
            <p><strong>Total Files:</strong> {len(files)}</p>
        </div>
    </div>

    <script>
        let allExpanded = false;
        
        function toggleNode(element) {{
            const content = element.nextElementSibling;
            const isVisible = content.style.display !== 'none';
            
            if (isVisible) {{
                content.style.display = 'none';
                element.innerHTML = element.innerHTML.replace('📂', '📁');
            }} else {{
                content.style.display = 'block';
                element.innerHTML = element.innerHTML.replace('📁', '📂');
            }}
        }}
        
        function toggleAll() {{
            const toggles = document.querySelectorAll('.tree-toggle');
            const contents = document.querySelectorAll('.tree-content');
            const button = document.querySelector('.expand-all-btn');
            
            if (allExpanded) {{
                // Collapse all
                contents.forEach(content => {{
                    content.style.display = 'none';
                }});
                toggles.forEach(toggle => {{
                    toggle.innerHTML = toggle.innerHTML.replace('📂', '📁');
                }});
                button.textContent = 'Expand All';
                allExpanded = false;
            }} else {{
                // Expand all
                contents.forEach(content => {{
                    content.style.display = 'block';
                }});
                toggles.forEach(toggle => {{
                    toggle.innerHTML = toggle.innerHTML.replace('📁', '📂');
                }});
                button.textContent = 'Collapse All';
                allExpanded = true;
            }}
        }}
        
        // Keyboard navigation
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Enter' || e.key === ' ') {{
                const focused = document.activeElement;
                if (focused.classList.contains('tree-toggle')) {{
                    e.preventDefault();
                    toggleNode(focused);
                }}
            }}
        }});
        
        // Make tree toggles focusable
        document.querySelectorAll('.tree-toggle').forEach(toggle => {{
            toggle.setAttribute('tabindex', '0');
        }});
    </script>
</body>
</html>"""
    
    return html_template

def main():
    """Main function to generate the archive HTML file."""
    print("Generating Times-Virginian Archive HTML file...")
    
    try:
        html_content = generate_html_page()
        
        output_file = "times_virginian_archive.html"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"Archive HTML file generated successfully: {output_file}")
        print(f"File size: {os.path.getsize(output_file)} bytes")
        
    except Exception as e:
        print(f"Error generating archive: {str(e)}")

if __name__ == "__main__":
    main() 