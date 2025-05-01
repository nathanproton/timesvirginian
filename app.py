from flask import Flask, render_template, request, jsonify, send_file
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

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

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

if __name__ == '__main__':
    app.run(debug=True, port=5004) 