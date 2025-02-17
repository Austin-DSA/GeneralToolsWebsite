from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

STATES = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
]


class NewEventForm(forms.Form):
    title = forms.CharField(
        label="Event title",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    description = forms.CharField(
        label="Description",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    startDate = forms.DateField(
        label="Start date", widget=forms.DateInput(attrs={"class": "form-field w-full"})
    )
    startTime = forms.TimeField(
        label="Start time", widget=forms.TimeInput(attrs={"class": "form-field w-full"})
    )
    endDate = forms.DateField(
        label="End date", widget=forms.DateInput(attrs={"class": "form-field w-full"})
    )
    endTime = forms.TimeField(
        label="End time", widget=forms.TimeInput(attrs={"class": "form-field w-full"})
    )
    instructions = forms.CharField(
        label="Instructions",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    locationName = forms.CharField(
        label="Location name",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    address = forms.CharField(
        label="Address", widget=forms.TextInput(attrs={"class": "form-field w-full"})
    )
    city = forms.CharField(
        label="City", widget=forms.TextInput(attrs={"class": "form-field w-full"})
    )
    choices = {state: state for state in STATES}
    state = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices=choices,
        initial="TX",
    )
    country = forms.CharField(
        label="Country", widget=forms.TextInput(attrs={"class": "form-field w-full"})
    )
    zipcode = forms.IntegerField(
        label="Zip code", widget=forms.NumberInput(attrs={"class": "form-field w-full"})
    )

    def clean_zipcode(self):
        data = self.cleaned_data["zipcode"]
        zip_str = str(data)

        if len(zip_str) == 5:
            return data

        else:
            raise ValidationError(_("Zip code must be five digits long"))
