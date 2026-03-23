/* ============================================================================
   AutoBill Buddy - Chart.js Helpers
============================================================================ */

const CHART_DEFAULTS = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
        legend: { display: false },
        tooltip: {
            backgroundColor: 'rgba(0,0,0,0.8)',
            titleFont: { family: "'Plus Jakarta Sans', sans-serif", size: 13 },
            bodyFont: { family: "'Plus Jakarta Sans', sans-serif", size: 12 },
            padding: 12,
            cornerRadius: 8,
            callbacks: {
                label: ctx => ' ₹' + ctx.parsed.y.toLocaleString('en-IN')
            }
        }
    }
};

// Create a bar chart for revenue
function createRevenueChart(canvasId, labels, values, title) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    // Destroy existing
    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    return new Chart(canvas, {
        type: 'bar',
        data: {
            labels: labels || [],
            datasets: [{
                label: title || 'Revenue',
                data: values || [],
                backgroundColor: 'rgba(16,185,129,0.8)',
                borderColor: '#10B981',
                borderWidth: 2,
                borderRadius: 8,
                hoverBackgroundColor: '#059669'
            }]
        },
        options: {
            ...CHART_DEFAULTS,
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(0,0,0,0.05)' },
                    ticks: {
                        callback: v => '₹' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v),
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 },
                        color: 'var(--text-muted)'
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 },
                        color: 'var(--text-muted)'
                    }
                }
            }
        }
    });
}

// Create a line chart with gradient fill
function createLineChart(canvasId, labels, values) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    const ctx = canvas.getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.offsetHeight || 200);
    gradient.addColorStop(0, 'rgba(16,185,129,0.3)');
    gradient.addColorStop(1, 'rgba(16,185,129,0.02)');

    return new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels || [],
            datasets: [{
                data: values || [],
                borderColor: '#10B981',
                backgroundColor: gradient,
                fill: true,
                tension: 0.4,
                pointRadius: 4,
                pointBackgroundColor: '#10B981',
                borderWidth: 2
            }]
        },
        options: {
            ...CHART_DEFAULTS,
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(0,0,0,0.05)' },
                    ticks: {
                        callback: v => '₹' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v),
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 },
                        color: 'var(--text-muted)'
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 },
                        color: 'var(--text-muted)'
                    }
                }
            }
        }
    });
}

// Create a pie chart
function createPieChart(canvasId, labels, values) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    const colors = ['#10B981', '#3B82F6', '#8B5CF6', '#F59E0B', '#EF4444', '#EC4899'];

    return new Chart(canvas, {
        type: 'pie',
        data: {
            labels: labels || [],
            datasets: [{
                data: values || [],
                backgroundColor: colors.slice(0, labels.length),
                borderWidth: 0,
                hoverOffset: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 10 },
                        color: 'var(--text-muted)',
                        padding: 8,
                        boxWidth: 10
                    }
                },
                tooltip: {
                    ...CHART_DEFAULTS.plugins.tooltip,
                    callbacks: {
                        label: ctx => ' ' + ctx.label + ': ' + ctx.parsed
                    }
                }
            }
        }
    });
}

// Create a line chart for trends
function createTrendChart(canvasId, labels, revenueValues, profitValues) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    return new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels || [],
            datasets: [
                {
                    label: 'Revenue',
                    data: revenueValues || [],
                    borderColor: '#10B981',
                    backgroundColor: 'rgba(16,185,129,0.08)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointBackgroundColor: '#10B981',
                    borderWidth: 2
                },
                {
                    label: 'Profit',
                    data: profitValues || [],
                    borderColor: '#8B5CF6',
                    backgroundColor: 'rgba(139,92,246,0.06)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointBackgroundColor: '#8B5CF6',
                    borderWidth: 2
                }
            ]
        },
        options: {
            ...CHART_DEFAULTS,
            plugins: {
                ...CHART_DEFAULTS.plugins,
                legend: {
                    display: true,
                    position: 'top',
                    labels: {
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 12 },
                        color: 'var(--text-muted)',
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 16
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(0,0,0,0.05)' },
                    ticks: {
                        callback: v => '₹' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v),
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 },
                        color: 'var(--text-muted)'
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 },
                        color: 'var(--text-muted)'
                    }
                }
            }
        }
    });
}

// Create doughnut chart for payment modes
function createPaymentDoughnut(canvasId, cash, udhaar) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    return new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: ['Cash', 'Udhaar'],
            datasets: [{
                data: [cash || 0, udhaar || 0],
                backgroundColor: ['#10B981', '#EF4444'],
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 12 },
                        color: 'var(--text-muted)',
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 16
                    }
                }
            }
        }
    });
}

window.createRevenueChart = createRevenueChart;
window.createTrendChart = createTrendChart;
window.createPaymentDoughnut = createPaymentDoughnut;
window.createLineChart = createLineChart;
window.createPieChart = createPieChart;
