from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth import get_user_model

User = get_user_model()

def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        # 1. Chercher l'utilisateur par email
        try:
            user_obj = User.objects.get(email=email)
        except User.DoesNotExist:
            user_obj = None

        # 2. Authentifier avec son username
        if user_obj:
            user = authenticate(request, username=user_obj.username, password=password)
        else:
            user = None

        if user is not None:
            login(request, user)
            messages.success(request, f"Bienvenue {user.username} !")
            return redirect('home')
        else:
            messages.error(request, "Email ou mot de passe incorrect.")

    return render(request, 'utilisateurs/login.html')

def logout_view(request):
    logout(request)
    messages.info(request, "Vous êtes déconnecté.")
    return redirect('home')


    