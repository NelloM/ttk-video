from django.shortcuts import render


def stampa_valori(campo1, campo2):
    print(f"Campo 1: {campo1} | Campo 2: {campo2}", flush=True)


def index(request):
    messaggio = None
    if request.method == "POST":
        campo1 = request.POST.get("campo1", "")
        campo2 = request.POST.get("campo2", "")
        stampa_valori(campo1, campo2)
        messaggio = "Valori stampati nei log del server."
    return render(request, "core/index.html", {"messaggio": messaggio})
