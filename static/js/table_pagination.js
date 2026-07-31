document.addEventListener('DOMContentLoaded', function() {
  const tables = document.querySelectorAll('table[data-paginate="true"]');
  
  tables.forEach(table => {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    
    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (rows.length === 0) return;
    
    // Set items per page (default 10)
    let perPage = parseInt(table.getAttribute('data-per-page')) || 10;
    let currentPage = 1;
    let totalPages = Math.ceil(rows.length / perPage);
    
    if (totalPages <= 1) return; // No pagination needed
    
    // Create pagination container
    const paginationContainer = document.createElement('div');
    paginationContainer.className = 'table-pagination';
    paginationContainer.style.display = 'flex';
    paginationContainer.style.justifyContent = 'space-between';
    paginationContainer.style.alignItems = 'center';
    paginationContainer.style.padding = '1rem 0.5rem 0.5rem';
    paginationContainer.style.borderTop = '1px solid #f1f5f9';
    paginationContainer.style.marginTop = '0.5rem';
    paginationContainer.style.fontSize = '0.85rem';
    
    // Insert after table (or its responsive wrapper if it has one)
    let wrapper = table.parentElement;
    if (wrapper.classList.contains('table-responsive') || wrapper.style.overflowX) {
      wrapper.parentElement.insertBefore(paginationContainer, wrapper.nextSibling);
    } else {
      table.parentElement.insertBefore(paginationContainer, table.nextSibling);
    }
    
    function renderTable() {
      // Hide all rows
      rows.forEach(row => row.style.display = 'none');
      
      // Show rows for current page
      const start = (currentPage - 1) * perPage;
      const end = start + perPage;
      for (let i = start; i < end && i < rows.length; i++) {
        rows[i].style.display = '';
      }
      
      renderControls();
    }
    
    function createButton(text, iconHtml, iconPos, disabled, onClick) {
      const btn = document.createElement('button');
      btn.type = 'button';
      
      if (iconPos === 'left') {
        btn.innerHTML = `${iconHtml}<span>${text}</span>`;
      } else {
        btn.innerHTML = `<span>${text}</span>${iconHtml}`;
      }
      
      btn.style.padding = '0.45rem 0.85rem';
      btn.style.border = '1px solid #e2e8f0';
      btn.style.background = disabled ? '#f8fafc' : '#ffffff';
      btn.style.color = disabled ? '#94a3b8' : '#334155';
      btn.style.borderRadius = '0.4rem';
      btn.style.cursor = disabled ? 'not-allowed' : 'pointer';
      btn.style.fontWeight = '600';
      btn.style.fontSize = '0.85rem';
      btn.style.display = 'inline-flex';
      btn.style.alignItems = 'center';
      btn.style.gap = '0.4rem';
      btn.style.transition = 'all 0.2s ease-in-out';
      btn.style.boxShadow = disabled ? 'none' : '0 1px 2px rgba(0,0,0,0.05)';
      
      if (!disabled) {
        btn.onmouseover = () => {
          btn.style.background = '#f1f5f9';
          btn.style.borderColor = '#cbd5e1';
          btn.style.color = '#0f172a';
        };
        btn.onmouseout = () => {
          btn.style.background = '#ffffff';
          btn.style.borderColor = '#e2e8f0';
          btn.style.color = '#334155';
        };
        btn.onclick = (e) => {
          e.preventDefault();
          onClick();
        };
      }
      return btn;
    }
    
    function renderControls() {
      paginationContainer.innerHTML = '';
      
      // Info section (Left)
      const info = document.createElement('div');
      info.style.color = '#64748b';
      info.style.fontWeight = '500';
      const start = (currentPage - 1) * perPage + 1;
      const end = Math.min(currentPage * perPage, rows.length);
      info.innerHTML = `Showing <span style="font-weight:600; color:#334155;">${start}</span> to <span style="font-weight:600; color:#334155;">${end}</span> of <span style="font-weight:600; color:#334155;">${rows.length}</span> results`;
      paginationContainer.appendChild(info);
      
      // Buttons section (Right)
      const btnGroup = document.createElement('div');
      btnGroup.style.display = 'flex';
      btnGroup.style.gap = '0.5rem';
      
      // Prev button
      const prevBtn = createButton('Previous', '<i class="fa-solid fa-chevron-left" style="font-size:0.75rem;"></i>', 'left', currentPage === 1, () => {
        currentPage--;
        renderTable();
      });
      btnGroup.appendChild(prevBtn);
      
      // Next button
      const nextBtn = createButton('Next', '<i class="fa-solid fa-chevron-right" style="font-size:0.75rem;"></i>', 'right', currentPage === totalPages, () => {
        currentPage++;
        renderTable();
      });
      btnGroup.appendChild(nextBtn);
      
      paginationContainer.appendChild(btnGroup);
    }
    
    renderTable();
  });
});
