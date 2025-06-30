from flask import Flask, render_template, request, jsonify, send_file, Response
import os
import fitz  # PyMuPDF
from pathlib import Path
import json
import boto3
from botocore.config import Config
from io import BytesIO
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import logging
import datetime
import xml.etree.ElementTree as ET
import threading
import time

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["2400 per day", "500 per hour"]
)

# Constants
MAX_LEN = 64  # Maximum length for search text
SECRET = os.getenv("API_SECRET")  # Bearer token for auth
TYPESENSE_ENDPOINT = "https://longlines.ai/submit"  # Typesense middleware endpoint

# DigitalOcean Spaces configuration
s3_client = boto3.client('s3',
    endpoint_url=f'https://{os.getenv("DIGITALOCEAN_SPACES_REGION")}.digitaloceanspaces.com',
    aws_access_key_id=os.getenv("DIGITALOCEAN_SPACES_KEY_ID"),
    aws_secret_access_key=os.getenv("DIGITALOCEAN_SPACES_SECRET"),
    config=Config(signature_version='s3v4')
)

SPACES_BUCKET = os.getenv("DIGITALOCEAN_SPACES_BUCKET")
SPACES_BASE_URL = f"https://{SPACES_BUCKET}.{os.getenv('DIGITALOCEAN_SPACES_REGION')}.digitaloceanspaces.com"

# PDF base directory (now using Spaces)
PDF_BASE_DIR = "tva"  # Base directory in Spaces
JSONL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_documents.jsonl")

# Local JSONL cache directory
LOCAL_JSONL_DIR = os.path.join(app.root_path, 'static', 'jsonl')
os.makedirs(LOCAL_JSONL_DIR, exist_ok=True)

# JSONL cache location in the bucket (tva/cache)
# Build dynamically using the same bucket/region configuration to avoid hard-coding.
CACHE_URL_BASE = f"{SPACES_BASE_URL}/{PDF_BASE_DIR}/cache"

def log_search(query, ip, user_agent, status, results_count=None):
    """Log search activity"""
    log_data = {
        'query': query,
        'ip': ip,
        'user_agent': user_agent,
        'status': status,
        'timestamp': datetime.datetime.now().isoformat()
    }
    if results_count is not None:
        log_data['results_count'] = results_count
    
    logger.info(f"Search log: {json.dumps(log_data)}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/jsonl')
def get_jsonl():
    """Serve the JSONL file"""
    try:
        return send_file(JSONL_FILE, mimetype='application/json')
    except Exception as e:
        return jsonify({'error': str(e)}), 404

@app.route('/search_jsonl')
@limiter.limit("120 per hour")  # 120 requests per hour for search endpoint
def search_jsonl_endpoint():
    """Direct JSONL file search endpoint"""
    query = request.args.get('q', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    
    if not query:
        return jsonify({'hits': [], 'found': 0})
    
    try:
        # First pass: count total matches and collect all matching indices
        matching_indices = []
        with open(JSONL_FILE, 'r') as f:
            for i, line in enumerate(f):
                doc = json.loads(line)
                if query.lower() in doc['text'].lower():
                    matching_indices.append(i)
        
        total_matches = len(matching_indices)
        
        # Calculate which indices we need for this page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_indices = matching_indices[start_idx:end_idx]
        
        # Second pass: get the actual documents for this page
        results = []
        with open(JSONL_FILE, 'r') as f:
            for i, line in enumerate(f):
                if i in page_indices:
                    doc = json.loads(line)
                    results.append({
                        'document': doc,
                        'highlights': [{
                            'field': 'text',
                            'snippet': doc['text'].replace(query, f'<mark>{query}</mark>'),
                            'matched_tokens': [query]
                        }]
                    })
                if i > max(page_indices) if page_indices else 0:
                    break
        
        return jsonify({
            'hits': results,
            'found': total_matches
        })
    except Exception as e:
        print(f"JSONL search error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/pdf/<path:filename>')
def get_pdf(filename):
    try:
        # Extract the collection name from the filename (everything before the last underscore)
        collection_name = '_'.join(filename.split('_')[:-1])
        s3_key = f"{PDF_BASE_DIR}/{collection_name}/{filename}"
        
        # Get the file from Spaces
        response = s3_client.get_object(Bucket=SPACES_BUCKET, Key=s3_key)
        pdf_data = response['Body'].read()
        
        return send_file(
            BytesIO(pdf_data),
            mimetype='application/pdf',
            as_attachment=False
        )
    except Exception as e:
        print(f"Error serving PDF: {str(e)}")
        return "PDF not found", 404

@app.route('/highlight')
def highlight():
    filename = request.args.get('file')
    bbox = request.args.get('bbox')
    page = int(request.args.get('page', 1))
    text = request.args.get('text', '')
    
    if not all([filename, bbox]):
        return "Missing parameters", 400
    
    try:
        # Extract the collection name from the filename
        collection_name = '_'.join(filename.split('_')[:-1])
        s3_key = f"{PDF_BASE_DIR}/{collection_name}/{filename}"
        
        print(f"Attempting to fetch PDF from Spaces: {s3_key}")  # Debug log
        
        # Get the file from Spaces
        try:
            response = s3_client.get_object(Bucket=SPACES_BUCKET, Key=s3_key)
            pdf_data = response['Body'].read()
            if not pdf_data:
                raise Exception("PDF data is empty")
        except Exception as e:
            print(f"Error fetching PDF from Spaces: {str(e)}")
            return f"Error fetching PDF: {str(e)}", 404
        
        # Parse bbox from string "[x0,y0,x1,y1]" to list of floats
        try:
            bbox = [float(x) for x in bbox.strip('[]').split(',')]
        except Exception as e:
            print(f"Error parsing bbox: {str(e)}")
            return "Invalid bbox format", 400
        
        # Open PDF from memory
        try:
            doc = fitz.open(stream=pdf_data, filetype="pdf")
            if page > len(doc):
                raise Exception(f"Page {page} is out of range. Document has {len(doc)} pages.")
            page = doc[page - 1]  # Convert to 0-based index
        except Exception as e:
            print(f"Error opening PDF: {str(e)}")
            return f"Error processing PDF: {str(e)}", 500
        
        # Create a new PDF with just this page
        new_doc = fitz.open()
        new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
        
        # Draw the original page content
        new_page.show_pdf_page(new_page.rect, doc, page.number)
        
        # Add rectangle annotation for the paragraph box
        rect = new_page.add_rect_annot(bbox)
        rect.set_colors({
            "stroke": (1, 0, 0),  # Red
            "fill": None
        })
        rect.set_border(width=1)  # Thin border
        rect.update()
        
        # Find the word in the text and highlight only that word
        if text:
            # Get all text instances on the page
            text_instances = page.search_for(text)
            for inst in text_instances:
                # Create highlight only for the word
                highlight = new_page.add_highlight_annot(inst)
                highlight.set_colors({
                    "stroke": (1, 1, 0),  # Yellow
                    "fill": (1, 1, 0.5)   # Light yellow fill
                })
                highlight.set_opacity(0.3)
                highlight.update()
        
        # Save to memory
        pdf_bytes = new_doc.tobytes()
        
        # Clean up
        doc.close()
        new_doc.close()
        
        return pdf_bytes, 200, {
            'Content-Type': 'application/pdf',
            'Content-Disposition': 'inline; filename=highlighted.pdf'
        }
    except Exception as e:
        print(f"Error highlighting PDF: {str(e)}")
        return f"Error processing PDF: {str(e)}", 500

@app.route("/submit", methods=["POST"])
@limiter.limit("60 per minute")  # More restrictive limit for API endpoint
def submit():
    logger.info("Received request to /submit")
    logger.info(f"Request headers: {dict(request.headers)}")
    logger.info(f"Request body: {request.get_data()}")
    
    data = request.get_json()
    if not data or "text" not in data:
        log_search(
            query='',
            ip=request.remote_addr,
            user_agent=request.user_agent.string,
            status='missing_input'
        )
        return Response("Missing input", status=400)

    text = data["text"]
    page = data.get("page", 1)  # Get page parameter, default to 1
    
    if not isinstance(text, str) or len(text) > MAX_LEN:
        log_search(
            query=text,
            ip=request.remote_addr,
            user_agent=request.user_agent.string,
            status='invalid_input'
        )
        return Response("Invalid input", status=400)

    # Basic sanitization
    sanitized = "".join(c for c in text if c.isprintable())

    try:
        # Make request to Typesense middleware
        headers = {
            "Authorization": f"Bearer {SECRET}",
            "Content-Type": "application/json"
        }
        logger.info(f"Sending request to {TYPESENSE_ENDPOINT}")
        logger.info(f"Request headers: {headers}")
        logger.info(f"Request body: {{'text': '{sanitized}', 'page': {page}}}")
        
        response = requests.post(
            TYPESENSE_ENDPOINT,
            json={"text": sanitized, "page": page},
            headers=headers
        )
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response headers: {response.headers}")
        logger.info(f"Response body: {response.text}")
        
        response.raise_for_status()
        
        # Parse JSONL response
        lines = response.text.strip().split('\n')
        search_results = []
        for line in lines:
            if line.strip():  # Skip empty lines
                search_results.append(json.loads(line))

        # Extract the actual search results from the nested structure
        actual_results = []
        if len(search_results) > 1 and 'results' in search_results[1]:
            actual_results = search_results[1]['results']

        # Log successful search
        log_search(
            query=sanitized,
            ip=request.remote_addr,
            user_agent=request.user_agent.string,
            status='success',
            results_count=len(actual_results)
        )

        # Create single JSON response
        response_data = {
            "query": sanitized,
            "results": actual_results
        }
        logger.info(f"Sending response back to client: {json.dumps(response_data)}")
        return jsonify(response_data)
    except Exception as e:
        # Log search error
        logger.error(f"Error in submit endpoint: {str(e)}")
        log_search(
            query=sanitized,
            ip=request.remote_addr,
            user_agent=request.user_agent.string,
            status=f'error: {str(e)}'
        )
        return Response(f"Search error: {str(e)}", status=500)

def download_jsonl_cache():
    """Download JSONL files from the remote cache to the local static/jsonl directory.

    Uses the authenticated `s3_client` so it works even if the bucket is private.
    """

    prefix = f"{PDF_BASE_DIR}/cache/"  # e.g. tva/cache/
    filenames = []
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=SPACES_BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.jsonl'):
                    continue

                filename = os.path.basename(key)
                filenames.append(filename)

                local_path = os.path.join(LOCAL_JSONL_DIR, filename)
                if os.path.exists(local_path):
                    continue  # already cached

                try:
                    response = s3_client.get_object(Bucket=SPACES_BUCKET, Key=key)
                    with open(local_path, 'wb') as f:
                        f.write(response['Body'].read())
                    logger.info(f"Cached {filename}")
                except Exception as download_err:
                    logger.error(f"Failed to download {key}: {download_err}")
        return filenames
    except Exception as e:
        logger.error(f"Error listing/downloading JSONL files: {str(e)}")
        return []

@app.route('/update_jsonl_cache')
@limiter.limit("10 per hour")
def update_jsonl_cache_route():
    """Refresh the local JSONL cache from the remote bucket."""
    files = download_jsonl_cache()
    return jsonify({'cached_files': files})

@app.route('/list_cached_jsonl')
def list_cached_jsonl():
    """Return list of cached JSONL filenames."""
    files = [f for f in os.listdir(LOCAL_JSONL_DIR) if f.endswith('.jsonl')]
    return jsonify({'files': sorted(files)})

@app.route('/jsonl_index')
@limiter.limit("10 per hour")
def jsonl_index():
    """Synchronize the JSONL cache with the remote bucket and provide a filename index.

    This endpoint will:
    1. Call `download_jsonl_cache()` to fetch any new *.jsonl files from the remote
       DigitalOcean Spaces bucket into the local `static/jsonl` directory.
    2. Build a list of all cached *.jsonl filenames (excluding the generated index file itself).
    3. Persist this list to `index-list.jsonl` inside the same directory so it can be served
       statically if needed.
    4. Return a JSON response `{ "files": [ ... ] }` containing the sorted list of filenames.
    """

    # Ensure the latest files are downloaded
    try:
        download_jsonl_cache()
    except Exception as e:
        logger.error(f"Error updating JSONL cache: {str(e)}")

    # Collect all jsonl files (ignore the index file itself if it already exists)
    files = [f for f in os.listdir(LOCAL_JSONL_DIR)
             if f.endswith('.jsonl') and f != 'index-list.jsonl']

    # Persist the index so it can be served statically if desired
    index_path = os.path.join(LOCAL_JSONL_DIR, 'index-list.jsonl')
    try:
        with open(index_path, 'w') as fp:
            for fname in files:
                fp.write(json.dumps({"file": fname}) + '\n')
        logger.info(f"Wrote JSONL index with {len(files)} entries to {index_path}")
    except Exception as e:
        logger.error(f"Failed to write JSONL index file: {str(e)}")

    # Return list to the client
    return jsonify({'files': sorted(files)})

@app.route('/preset_jsonl')
def preset_jsonl():
    """Serve paginated results from a cached JSONL file."""
    file = request.args.get('file')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    if not file:
        return jsonify({'error': 'file parameter required'}), 400
    local_path = os.path.join(LOCAL_JSONL_DIR, file)
    if not os.path.exists(local_path):
        return jsonify({'error': 'file not found'}), 404
    try:
        # Read required page lines
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        hits = []
        total = 0
        with open(local_path, 'r') as f:
            for i, line in enumerate(f):
                if start_idx <= i < end_idx:
                    hits.append(json.loads(line))
                total += 1
                if i >= end_idx:
                    continue
        return jsonify({'query': file, 'results': hits, 'found': total})
    except Exception as e:
        logger.error(f"Error reading preset JSONL {file}: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Archive generation functions
def get_all_files_in_tva_for_archive():
    """Get all files in the tva directory from DigitalOcean Spaces, excluding .code-workspace files."""
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
        logger.error(f"Error listing files: {str(e)}")
    return files

def generate_signed_url_for_archive(key, expiration_days=7):
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
        logger.error(f"Error generating signed URL for {key}: {str(e)}")
        return None

def build_tree_structure_for_archive(files):
    """Build a nested tree structure from the file list."""
    from collections import defaultdict
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

def generate_tree_html_for_archive(tree_data, level=0):
    """Generate HTML for the tree structure."""
    html = ""
    
    if level == 0:
        # Root level
        if tree_data['root']['files']:
            html += '<ul class="tree-list">\n'
            for file_info in sorted(tree_data['root']['files'], key=lambda x: x['key']):
                filename = os.path.basename(file_info['key'])
                signed_url = generate_signed_url_for_archive(file_info['key'])
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
                signed_url = generate_signed_url_for_archive(file_info['key'])
                if signed_url:
                    html += f'            <li class="tree-item file-item"><a href="{signed_url}" target="_blank">{filename}</a></li>\n'
            
            html += f'        </ul>\n'
            html += f'    </div>\n'
            html += f'</div>\n'
    
    return html

def generate_archive_html():
    """Generate the complete HTML page for the archive."""
    created_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    expiry_date = (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    logger.info("Fetching file list from DigitalOcean Spaces for archive...")
    files = get_all_files_in_tva_for_archive()
    logger.info(f"Found {len(files)} files for archive")
    
    logger.info("Building tree structure for archive...")
    tree_data = build_tree_structure_for_archive(files)
    
    logger.info("Generating signed URLs and HTML for archive...")
    tree_html = generate_tree_html_for_archive(tree_data)
    
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

@app.route('/generate_archive')
@limiter.limit("5 per hour")
def generate_archive():
    """Generate the Times-Virginian Archive HTML file with authentication and rate limiting."""
    # Check for authentication
    password = request.args.get('key')
    direct_key = os.getenv('DIRECT_KEY')
    
    if not password or password != direct_key:
        logger.warning(f"Unauthorized archive generation attempt from {request.remote_addr}")
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Check if file exists and is less than 6 days old
    archive_file = "times_virginian_archive.html"
    if os.path.exists(archive_file):
        file_age = datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(archive_file))
        if file_age.days < 6:
            logger.info(f"Archive file is {file_age.days} days old, serving existing file")
            return send_file(archive_file, mimetype='text/html')
    
    try:
        logger.info("Generating new Times-Virginian Archive HTML file...")
        html_content = generate_archive_html()
        
        with open(archive_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"Archive HTML file generated successfully: {archive_file}")
        logger.info(f"File size: {os.path.getsize(archive_file)} bytes")
        
        return send_file(archive_file, mimetype='text/html')
        
    except Exception as e:
        logger.error(f"Error generating archive: {str(e)}")
        return jsonify({'error': f'Error generating archive: {str(e)}'}), 500

# Start keep-alive background task

# ---------------------------------------------------------------------------
# Background keep-alive thread
# ---------------------------------------------------------------------------


def _keep_tunnel_alive():
    """Periodically hit longlines.ai every minute to keep the tunnel alive."""
    url = "https://longlines.ai/"
    while True:
        try:
            requests.get(url, timeout=10)
            logger.debug("Keep-alive ping to longlines.ai successful")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")
        time.sleep(60)


# Launch the keep-alive thread as a daemon so it doesn't block shutdown
_keep_alive_thread = threading.Thread(target=_keep_tunnel_alive, daemon=True)
_keep_alive_thread.start()

if __name__ == '__main__':
    app.run(debug=True, port=5004) 