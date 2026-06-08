from django import forms
from .EventAutomation import EventAutomationDriver, ActionNetworkAutomation
from .models import User, EventOwners

class GuestLoginForm(forms.Form):
    class Keys:
        EMAIL = "email"
        NAME = "name"

    email = forms.EmailField(
        label="DSA Email Address",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        required=True,
    )
    
    name = forms.CharField(
        label="Name",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        required=True,
    )

    def getEmail(self) -> str | None:
        if not self.is_valid():
            return None
        return self.cleaned_data[GuestLoginForm.Keys.EMAIL]

    def getName(self) -> str | None:
        if not self.is_valid():
            return None
        return self.cleaned_data[GuestLoginForm.Keys.NAME]