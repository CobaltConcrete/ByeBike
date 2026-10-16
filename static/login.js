document.getElementById('loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button');
  button.disabled = true;
  try {
    const response = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:document.getElementById('password').value})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Sign-in failed.');
    location.assign('/');
  } catch (error) { document.getElementById('loginError').textContent = error.message; }
  finally { button.disabled = false; }
});
