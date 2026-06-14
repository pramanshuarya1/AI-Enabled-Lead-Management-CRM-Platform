/* ============================================================
   TFU CRM — Call Form JavaScript
   Handles the dynamic connected/not-connected toggle
   and live amount summary calculator
   ============================================================ */

document.addEventListener('DOMContentLoaded', function () {

  // Set minimum date picker value to today
  const fuDateInput = document.getElementById('follow_up_date');
  if (fuDateInput) {
    const today = new Date();
    const pad = n => String(n).padStart(2, '0');
    const todayStr = `${today.getFullYear()}-${pad(today.getMonth()+1)}-${pad(today.getDate())}`;
    fuDateInput.min = todayStr;
  }

  // ── Connection Toggle ──────────────────────────────────────
  const connYes = document.getElementById('conn_yes');
  const connNo  = document.getElementById('conn_no');
  const connectedSection    = document.getElementById('connectedSection');
  const notConnectedSection = document.getElementById('notConnectedSection');

  function applyConnectionToggle() {
    const isConnected = connYes && connYes.checked;
    if (connectedSection)    connectedSection.classList.toggle('hidden', !isConnected);
    if (notConnectedSection) notConnectedSection.classList.toggle('hidden', isConnected);

    // Clear irrelevant required fields
    if (!isConnected) {
      hideSection('followUpSection');
      hideSection('convertedSection');
    }
  }

  if (connYes) connYes.addEventListener('change', applyConnectionToggle);
  if (connNo)  connNo.addEventListener('change',  applyConnectionToggle);
  applyConnectionToggle(); // Initial state

  // ── Call Status → Sub-sections ────────────────────────────
  const callStatus = document.getElementById('call_status');
  if (callStatus) {
    callStatus.addEventListener('change', function () {
      const val = this.value;
      toggleSection('followUpSection',  val === 'follow_up');
      toggleSection('convertedSection', val === 'converted');
    });
  }

  // ── Amount Summary Live Calculator ────────────────────────
  const priceField    = document.getElementById('bootcamp_price');
  const discountField = document.getElementById('discount_amount');
  const tokenField    = document.getElementById('token_amount');
  const totalField    = document.getElementById('amount_paid');

  function fmt(val) {
    const n = parseFloat(val) || 0;
    return '₹' + n.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  function updateSummary() {
    const price    = parseFloat(priceField?.value)    || 0;
    const discount = parseFloat(discountField?.value) || 0;
    const token    = parseFloat(tokenField?.value)    || 0;

    const netPrice = Math.max(0, price - discount);   // after discount
    // ── Total Paid logic ─────────────────────────────────────
    // If token > 0  → partial payment, total paid = token amount
    // If token == 0 → full payment,    total paid = price − discount
    const totalPaid = token > 0 ? token : netPrice;
    const balance   = Math.max(0, netPrice - token);

    // Update summary widget
    const sumPrice    = document.getElementById('sumPrice');
    const sumDiscount = document.getElementById('sumDiscount');
    const sumToken    = document.getElementById('sumToken');
    const sumTotal    = document.getElementById('sumTotal');
    const sumBalance  = document.getElementById('sumBalance');

    if (sumPrice)    sumPrice.textContent    = fmt(price);
    if (sumDiscount) sumDiscount.textContent = discount > 0 ? '- ' + fmt(discount) : fmt(0);
    if (sumToken)    sumToken.textContent    = token > 0 ? fmt(token) : fmt(0);
    if (sumTotal)    sumTotal.textContent    = fmt(totalPaid);
    if (sumBalance && token > 0) {
      sumBalance.textContent  = fmt(balance);
      sumBalance.closest?.('.sum-balance-row')?.style.setProperty('display', '');
    } else if (sumBalance) {
      sumBalance.closest?.('.sum-balance-row')?.style.setProperty('display', 'none');
    }

    // ── Auto-fill the Total Amount Paid input ────────────────
    // Always auto-fill when price, discount, or token changes
    if (totalField) {
      totalField.value = totalPaid > 0 ? totalPaid.toFixed(0) : '';
      // Flash the field to show it updated
      totalField.style.transition = 'border-color 0.3s';
      totalField.style.borderColor = 'var(--text-accent)';
      setTimeout(() => { totalField.style.borderColor = ''; }, 600);
    }
  }

  // Always recalculate when any source field changes (no manual lock)
  if (priceField)    priceField.addEventListener('input',    updateSummary);
  if (discountField) discountField.addEventListener('input', updateSummary);
  if (tokenField)    tokenField.addEventListener('input',    updateSummary);

  // Run once on load to populate from existing values (e.g. pre-filled bootcamp price)
  updateSummary();


  // ── Form Validation Before Submit ─────────────────────────
  const callForm = document.getElementById('callForm');
  if (callForm) {
    callForm.addEventListener('submit', function (e) {
      const isConnected = connYes && connYes.checked;

      if (isConnected) {
        const status = callStatus?.value;
        if (!status) {
          e.preventDefault();
          showFormError('Please select a call outcome/status');
          return;
        }

        if (status === 'converted') {
          const paid = parseFloat(totalField?.value) || 0;
          if (paid <= 0) {
            e.preventDefault();
            showFormError('Please enter the amount paid for conversion');
            return;
          }
        }

        if (status === 'follow_up') {
          const fuDate = document.getElementById('follow_up_date')?.value;
          const fuTime = document.getElementById('follow_up_time')?.value;
          if (!fuDate) {
            e.preventDefault();
            showFormError('Please set a follow-up date');
            return;
          }
          
          const today = new Date();
          const pad = n => String(n).padStart(2, '0');
          const todayStr = `${today.getFullYear()}-${pad(today.getMonth()+1)}-${pad(today.getDate())}`;
          
          if (fuDate < todayStr) {
            e.preventDefault();
            showFormError('Follow-up date cannot be in the past');
            return;
          }
          
          if (fuDate === todayStr && fuTime) {
            const currentTimeStr = `${pad(today.getHours())}:${pad(today.getMinutes())}`;
            if (fuTime < currentTimeStr) {
              e.preventDefault();
              showFormError('Follow-up time cannot be in the past');
              return;
            }
          }
        }
      }

      // Loading state
      const btn = document.getElementById('submitBtn');
      if (btn) {
        btn.disabled = true;
        btn.textContent = '💾 Saving...';
      }
    });
  }

  // ── Helpers ───────────────────────────────────────────────
  function toggleSection(id, show) {
    const el = document.getElementById(id);
    if (el) {
      if (show) {
        el.classList.remove('hidden');
        el.style.animation = 'fadeIn 0.2s ease forwards';
      } else {
        el.classList.add('hidden');
      }
    }
  }

  function hideSection(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
  }

  function showFormError(msg) {
    // Use toast if available, else alert
    if (typeof showToast === 'function') {
      showToast(msg, 'error');
    } else {
      alert(msg);
    }
  }

  // ── Disposition auto-suggestions ──────────────────────────
  const dispositionField = document.getElementById('disposition');
  if (callStatus && dispositionField && dispositionField.tagName === 'INPUT') {
    const dispositionSuggestions = {
      follow_up:       ['Call back tomorrow', 'Will decide after discussing', 'Needs time to think', 'Husband/Wife to discuss'],
      converted:       ['Paid on call', 'Token paid, balance pending', 'Full payment done'],
      already_enrolled: ['Already enrolled earlier', 'Enrolled for same Bx', 'Double enrollment found'],
      need_more_detail: ['Wants more info on curriculum', 'Asked for demo first', 'Needs ROI details'],
      not_interested:  ['Not interested at all', 'Budget constraint', 'Already doing similar course'],
      discarded:       ['Unreachable multiple times', 'Wrong number', 'Requested not to call'],
    };

    callStatus.addEventListener('change', function () {
      const suggestions = dispositionSuggestions[this.value] || [];
      if (suggestions.length > 0 && !dispositionField.value) {
        dispositionField.placeholder = suggestions[0];
      }
    });
  }

});

// ── Fade-in animation ──────────────────────────────────────
const style = document.createElement('style');
style.textContent = `
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(-8px); }
    to   { opacity: 1; transform: translateY(0); }
  }
`;
document.head.appendChild(style);
