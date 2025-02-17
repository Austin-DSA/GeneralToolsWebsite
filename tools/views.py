from django.shortcuts import render
from django.http import HttpResponseRedirect
from .forms import NewEventForm
import datetime
import time


def index(request):
    return render(request, "tools/home.html", {})


def new_event(request):
    if request.method == "POST":
        form = NewEventForm(request.POST)
        if form.is_valid():
            return HttpResponseRedirect("/")
    else:
        form = NewEventForm(
            initial={
                "startDate": datetime.date.today(),
                "startTime": datetime.date.fromtimestamp(time.time()),
                "endDate": datetime.date.today(),
                "endTime": datetime.date.fromtimestamp(time.time()),
            }
        )
    return render(request, "tools/new-event.html", {"form": form})
