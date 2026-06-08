function commonUI_displayLoadingScreen()
{
    const loader = document.getElementById('loader');
    loader.style.display = 'block';
}

function commonUI_hideLoadingScreen()
{
    const loader = document.getElementById('loader');
    loader.style.display = 'none';
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie('csrftoken');

async function sendInternalRequest(url,method,body) {
     const response = await fetch(url, {
        method: method,
        headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
        body: body,
        credentials: 'same-origin'
    });
    return response;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}