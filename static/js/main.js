document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.getElementById('searchInput');
    const searchButton = document.getElementById('searchButton');
    const searchResults = document.getElementById('searchResults');
    const pdfFrame = document.getElementById('pdfFrame');
    const loadMoreButton = document.getElementById('loadMoreButton');
    
    let currentPage = 1;
    const perPage = 10;
    let isLoading = false;
    let hasMoreResults = true;
    let allResults = [];

    // Reset search state
    function resetSearch() {
        currentPage = 1;
        hasMoreResults = true;
        allResults = [];
        searchResults.innerHTML = '';
        loadMoreButton.style.display = 'none';
        loadMoreButton.disabled = false;
        loadMoreButton.textContent = 'Load More Results';
        // Show the masonry overlay
        const overlay = document.getElementById('pdfMasonryOverlay');
        if (overlay) overlay.classList.remove('hidden');
    }

    // Handle search button click
    searchButton.addEventListener('click', () => {
        resetSearch();
        performSearch();
    });

    // Handle Enter key in search input
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            resetSearch();
            performSearch();
        }
    });

    // Handle Load More button click
    loadMoreButton.addEventListener('click', () => {
        if (!isLoading && hasMoreResults) {
            currentPage++;
            performSearch();
        }
    });

    function createResultItem(hit, searchMethod) {
        const resultItem = document.createElement('div');
        resultItem.className = 'list-group-item search-result-item';
        
        const doc = hit.document || hit; // Handle both Typesense and JSONL formats
        const highlights = hit.highlights || [];
        const textHighlight = highlights.find(h => h.field === 'text');
        
        let highlightedText = doc.text;
        if (textHighlight && textHighlight.snippet) {
            highlightedText = textHighlight.snippet;
        }
        
        // Add search method badge
        const methodBadge = `<span class=\"badge bg-info float-end\">${searchMethod}</span>`;
        
        resultItem.innerHTML = `
            <div class=\"d-flex justify-content-between align-items-center\">
                <div>
                    <strong>${doc.file}</strong>
                    <div class=\"text-muted\">Page ${doc.page}</div>
                </div>
                ${methodBadge}
            </div>
            <div class=\"mt-2\">${highlightedText}</div>
        `;
        
        resultItem.addEventListener('click', () => {
            // Remove active class from all items
            document.querySelectorAll('.search-result-item').forEach(item => {
                item.classList.remove('active');
            });
            // Add active class to clicked item
            resultItem.classList.add('active');
            
            // Load PDF with highlight
            loadPDFWithHighlight(doc);
        });
        
        return resultItem;
    }

    // Perform search
    async function performSearch() {
        if (isLoading) return;
        
        const query = searchInput.value.trim();
        if (!query) {
            resetSearch();
            return;
        }
        
        isLoading = true;
        loadMoreButton.disabled = true;
        loadMoreButton.textContent = 'Loading...';
        
        // Create loading indicator
        const loadingIndicator = document.createElement('div');
        loadingIndicator.className = 'loading-indicator p-3 text-center';
        loadingIndicator.innerHTML = `
            <div class=\"spinner-border text-primary\" role=\"status\">
                <span class=\"visually-hidden\">Loading...</span>
            </div>
            <div class=\"mt-2\">Searching...</div>
            <div class=\"alert alert-info mt-3\" role=\"alert\">
                This is V1, it might be slow.
            </div>
        `;
        
        if (currentPage === 1) {
            searchResults.innerHTML = '';
            searchResults.appendChild(loadingIndicator);
        }
        
        try {
            // Always use JSONL search endpoint
            const endpoint = '/search_jsonl';
            const response = await fetch(`${endpoint}?q=${encodeURIComponent(query)}&page=${currentPage}&per_page=${perPage}`);
            const data = await response.json();
            
            if (data.error) {
                throw new Error(data.error);
            }
            
            if (data.hits && data.hits.length > 0) {
                searchResults.innerHTML = ''; // Clear loading indicator
                
                // Add search method indicator
                const searchMethod = 'JSONL';
                const indicator = document.createElement('div');
                indicator.className = 'search-method-indicator p-3';
                indicator.textContent = `Using ${searchMethod} Search`;
                searchResults.appendChild(indicator);
                
                data.hits.forEach(hit => {
                    const resultItem = createResultItem(hit, searchMethod);
                    searchResults.appendChild(resultItem);
                });
                
                hasMoreResults = data.hits.length === perPage;
                loadMoreButton.style.display = hasMoreResults ? 'block' : 'none';
            } else {
                hasMoreResults = false;
                loadMoreButton.style.display = 'none';
                if (currentPage === 1) {
                    searchResults.innerHTML = ''; // Clear loading indicator
                    const noResults = document.createElement('div');
                    noResults.className = 'text-center p-3';
                    noResults.textContent = 'No results found';
                    searchResults.appendChild(noResults);
                }
            }
        } catch (error) {
            console.error('Search error:', error);
            searchResults.innerHTML = ''; // Clear loading indicator
            const errorDiv = document.createElement('div');
            errorDiv.className = 'alert alert-danger m-3';
            errorDiv.textContent = `Error: ${error.message}`;
            searchResults.appendChild(errorDiv);
            loadMoreButton.style.display = 'none';
        } finally {
            isLoading = false;
            loadMoreButton.disabled = false;
            loadMoreButton.textContent = 'Load More Results';
        }
    }

    function loadPDFWithHighlight(doc) {
        const highlightUrl = `/highlight?file=${encodeURIComponent(doc.file)}&page=${doc.page}&bbox=${encodeURIComponent(JSON.stringify(doc.bbox))}&text=${encodeURIComponent(doc.text)}`;
        pdfFrame.src = highlightUrl;
        // Hide the masonry overlay
        const overlay = document.getElementById('pdfMasonryOverlay');
        if (overlay) overlay.classList.add('hidden');
    }

    // Show overlay if PDF is cleared (optional, e.g. on reset)
    function showMasonryOverlay() {
        const overlay = document.getElementById('pdfMasonryOverlay');
        if (overlay) overlay.classList.remove('hidden');
    }
}); 