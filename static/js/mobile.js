document.addEventListener('DOMContentLoaded', function() {
    // DOM elements
    const searchInput = document.getElementById('searchInput');
    const searchButton = document.getElementById('searchButton');
    const searchResults = document.getElementById('searchResults');
    const loadMoreButton = document.getElementById('loadMoreButton');
    const loadMoreContainer = document.getElementById('loadMoreContainer');

    const pdfModal = document.getElementById('pdfModal');
    const pdfFrame = document.getElementById('pdfFrame');
    const backToResults = document.getElementById('backToResults');
    const searchInterface = document.getElementById('searchInterface');

    // Search state
    let currentQuery = '';
    let currentPage = 1;
    let totalResults = 0;
    let isLoading = false;


    // Event listeners
    searchButton.addEventListener('click', handleSearch);
    searchInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            handleSearch();
        }
    });
    loadMoreButton.addEventListener('click', loadMoreResults);
    backToResults.addEventListener('click', closeModal);

    // Handle search
    function handleSearch() {
        const query = searchInput.value.trim();
        if (!query) return;
        
        currentQuery = query;
        currentPage = 1;
        
        performSearch();
    }



    // Perform regular search
    async function performSearch() {
        if (isLoading) return;
        
        isLoading = true;
        loadMoreButton.disabled = true;
        loadMoreButton.textContent = 'Loading...';
        
        try {
            if (currentPage === 1) {
                searchResults.innerHTML = '<div class="text-center p-3"><div class="spinner-border" role="status"></div></div>';
            }
            
            const response = await fetch('/submit', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    text: currentQuery,
                    page: currentPage
                })
            });
            
            if (!response.ok) {
                throw new Error('Search failed');
            }
            
            const data = await response.json();
            displayResults(data, currentPage === 1);
            
        } catch (error) {
            console.error('Search error:', error);
            searchResults.innerHTML = '<div class="alert alert-danger m-3">Error: ' + error.message + '</div>';
            loadMoreButton.style.display = 'none';
        } finally {
            isLoading = false;
            loadMoreButton.disabled = false;
            loadMoreButton.textContent = 'Load More Results';
        }
    }



    // Display search results
    function displayResults(data, clearPrevious = false) {
        if (clearPrevious) {
            searchResults.innerHTML = '';
            totalResults = 0;
        }
        
        const results = data.results || [];
        totalResults += results.length;
        
        if (results.length === 0 && clearPrevious) {
            searchResults.innerHTML = '<div class="alert alert-info m-3">No results found</div>';
            loadMoreButton.style.display = 'none';
            return;
        }
        
        results.forEach(result => {
            const doc = result.document || result;
            const resultItem = createResultItem(doc);
            searchResults.appendChild(resultItem);
        });
        
        // Show/hide load more button
        if (results.length === 10) {
            loadMoreButton.style.display = 'block';
        } else {
            loadMoreButton.style.display = 'none';
        }
    }

    // Create result item
    function createResultItem(doc) {
        const resultItem = document.createElement('div');
        resultItem.className = 'search-result-item';
        
        const snippet = doc.text || '';
        const truncatedSnippet = snippet.length > 200 ? snippet.substring(0, 200) + '...' : snippet;
        
        resultItem.innerHTML = `
            <div class="fw-bold">${doc.file || 'Unknown File'}</div>
            <small class="text-muted">Page ${doc.page || 1}</small>
            <div class="mt-1">${truncatedSnippet}</div>
        `;
        
        resultItem.addEventListener('click', () => {
            // Remove active class from all items
            document.querySelectorAll('.search-result-item').forEach(item => {
                item.classList.remove('active');
            });
            // Add active class to clicked item
            resultItem.classList.add('active');
            
            // Open PDF in modal
            openPDFModal(doc);
        });
        
        return resultItem;
    }

    // Open PDF modal
    async function openPDFModal(doc) {
        try {
            const response = await fetch('/highlight', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    file: doc.file,
                    page: doc.page,
                    bbox: doc.bbox,
                    text: doc.text
                })
            });
            
            if (response.ok) {
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                pdfFrame.src = url;
                
                // Clean up the previous blob URL if it exists
                if (pdfFrame.currentBlobUrl) {
                    URL.revokeObjectURL(pdfFrame.currentBlobUrl);
                }
                pdfFrame.currentBlobUrl = url;
                
                // Show modal with animation
                pdfModal.style.display = 'flex';
                setTimeout(() => {
                    pdfModal.classList.add('showing');
                }, 10);
                
                // Prevent body scrolling
                document.body.style.overflow = 'hidden';
            } else {
                console.error('Error loading PDF:', response.statusText);
                alert('Error loading PDF. Please try again.');
            }
        } catch (error) {
            console.error('Error loading PDF:', error);
            alert('Error loading PDF. Please try again.');
        }
    }

    // Close PDF modal
    function closeModal() {
        pdfModal.classList.remove('showing');
        pdfModal.classList.add('hiding');
        
        setTimeout(() => {
            pdfModal.style.display = 'none';
            pdfModal.classList.remove('hiding');
            
            // Clean up blob URL
            if (pdfFrame.currentBlobUrl) {
                URL.revokeObjectURL(pdfFrame.currentBlobUrl);
                pdfFrame.currentBlobUrl = null;
            }
            
            pdfFrame.src = '';
            
            // Restore body scrolling
            document.body.style.overflow = 'auto';
        }, 300);
    }

    // Load more results
    function loadMoreResults() {
        currentPage++;
        performSearch();
    }



    // Handle back button/escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && pdfModal.style.display === 'flex') {
            closeModal();
        }
    });

    // Clean up blob URLs when page is unloaded
    window.addEventListener('beforeunload', () => {
        if (pdfFrame.currentBlobUrl) {
            URL.revokeObjectURL(pdfFrame.currentBlobUrl);
        }
    });
});

// Function for details toggle (keeping for compatibility)
function toggleDetails(event) {
    event.preventDefault();
    const content = document.getElementById('detailsContent');
    if (content.style.display === 'none') {
        content.style.display = 'block';
    } else {
        content.style.display = 'none';
    }
} 