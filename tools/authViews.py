import logging

from django.contrib.auth import login
from django.shortcuts import render, redirect

from .forms import RegisterForm

logger = logging.getLogger(__name__)


def register(request):
    """Self-service account creation.

    New accounts are active immediately but carry no permissions — the home
    menu stays empty until access is granted (see accessViews for the
    self-service request flow).
    """
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            logger.info("Register: Created new account %s", user.getUserNameString())
            login(request, user)
            return redirect("index")
        logger.info("Register: Submitted form is not valid")
        return render(request, "registration/newAccount.html", {"form": form})

    return render(request, "registration/newAccount.html", {"form": RegisterForm()})
