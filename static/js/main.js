document.addEventListener('DOMContentLoaded', function() {
    
    // ===== TOGGLE PASSWORD =====
    document.querySelectorAll('.toggle-password').forEach(btn => {
        btn.addEventListener('click', function() {
            const input = this.closest('.password-wrapper').querySelector('input');
            const icon = this.querySelector('i');
            if (input.type === 'password') {
                input.type = 'text';
                icon.classList.replace('ti-eye', 'ti-eye-off');
            } else {
                input.type = 'password';
                icon.classList.replace('ti-eye-off', 'ti-eye');
            }
        });
    });

    // ===== DROP ZONE =====
    document.querySelectorAll('.drop-zone').forEach(zone => {
        const input = zone.querySelector('input[type="file"]');
        const fileInfo = zone.closest('form').querySelector('.file-info');
        const fileName = fileInfo?.querySelector('.file-name');
        const fileSize = fileInfo?.querySelector('.file-size');

        zone.addEventListener('click', () => input?.click());

        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('dragover');
        });

        zone.addEventListener('dragleave', () => {
            zone.classList.remove('dragover');
        });

        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (input && files.length) {
                input.files = files;
                updateFileInfo(files[0]);
            }
        });

        input?.addEventListener('change', function() {
            if (this.files.length) updateFileInfo(this.files[0]);
        });

        function updateFileInfo(file) {
            if (!fileInfo) return;
            fileInfo.style.display = 'flex';
            if (fileName) fileName.textContent = file.name;
            if (fileSize) fileSize.textContent = `(${(file.size / 1024 / 1024).toFixed(2)} Mo)`;
            zone.querySelector('.drop-zone-content').style.display = 'none';
        }
    });

    // ===== VALIDATION GRILLE - Calcul auto =====
    const totalDisplay = document.getElementById('total-points');
    
    function updateTotals() {
        let grandTotal = 0;
        document.querySelectorAll('.groupe-card').forEach(groupe => {
            const noteEl = groupe.querySelector('.groupe-note strong');
            const inputs = groupe.querySelectorAll('.input-note');
            let sum = 0;
            inputs.forEach(inp => sum += parseFloat(inp.value) || 0);
            if (noteEl) noteEl.textContent = sum.toFixed(1);
            grandTotal += sum;
        });
        if (totalDisplay) totalDisplay.textContent = grandTotal.toFixed(1);
    }

    document.querySelectorAll('.input-note').forEach(input => {
        input.addEventListener('change', updateTotals);
        input.addEventListener('keyup', updateTotals);
    });

    // Calcul initial
    updateTotals();

    // ===== CHECKBOX CASCADE (Groupe -> Sous-critères) =====
    document.querySelectorAll('.groupe-checkbox').forEach(cb => {
        cb.addEventListener('change', function() {
            const groupeId = this.dataset.groupeId;
            const checked = this.checked;
            document.querySelectorAll(`.sous-checkbox[data-groupe-id="${groupeId}"]`).forEach(sc => {
                sc.checked = checked;
            });
        });
    });

    // ===== TOUT VALIDER =====
    window.toutValider = function() {
        document.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
    };

    // ===== MODALS DATA TRANSFER =====
    document.querySelectorAll('[data-bs-toggle="modal"]').forEach(btn => {
        btn.addEventListener('click', function() {
            const target = this.dataset.bsTarget;
            const modal = document.querySelector(target);
            if (!modal) return;
            
            // Transfère les data-* vers les inputs du modal
            Object.keys(this.dataset).forEach(key => {
                const input = modal.querySelector(`[data-field="${key}"]`);
                if (input) input.value = this.dataset[key];
            });
        });
    });

    // ===== ALERTS AUTO-DISMISS =====
    setTimeout(() => {
        document.querySelectorAll('.alert-dismissible').forEach(alert => {
            if (typeof bootstrap === 'undefined' || !bootstrap?.Alert) return;
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            bsAlert?.close();
        });
    }, 5000);

    // ===== MOBILE SIDEBAR TOGGLE =====
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');
    sidebarToggle?.addEventListener('click', () => {
        sidebar?.classList.toggle('open');
    });
});
