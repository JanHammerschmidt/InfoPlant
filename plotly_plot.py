from math import floor
from time import time

def init_plotly():
    from sys import stdout
    stdout.write("init plotly..")
    global plot, Scatter, Layout, Bar, Xaxis, YAxis, Legend, Marker
    global bar, scatter, layout, data, last_bar
    from plotly.offline import plot
    from plotly.graph_objs import Scatter, Layout, Bar, XAxis, YAxis, Legend, Marker

    # data
    bar = Bar(x=[], y=[], name='Aktueller Verbrauch')
    last_bar = Bar(x=[], y=[], name='Bisheriger Verbrauch<br>in aktueller Stunde', marker=dict(color='rgb(159, 197, 232)'))
    scatter = Scatter(x=[], y=[], name='Durchschnittlicher Verbrauch', mode="lines+markers")

    #layout
    layout = Layout(
        title="Stromverbrauch der letzten 24 Stunden", barmode='stacked',
        xaxis= XAxis(tickmode='array', ticktext=[], tickvals=[], range=[-0.5,1], tickangle=-45),
        yaxis= YAxis(title='Wh'),
        legend=Legend(bordercolor='#FFFFFF', borderwidth=3, xanchor="right") #x=1.0,y=1.0 bgcolor='#E2E2E2'
    )

    data = {
        "data": [bar,scatter,last_bar],
        "layout": layout
    }
    print(" done")


def plot_plotly(current_consumption, avg_consumption, xticks):
    bar.x = list(range(1,len(current_consumption)*2-2,2))
    bar.y = current_consumption[:-1]
    last_bar.x = [len(current_consumption)*2-1]
    last_bar.y = [current_consumption[-1]]

    scatter.x = list(range(1,len(avg_consumption)*2,2))
    scatter.y = avg_consumption

    xaxis = layout.xaxis
    for i in range(len(xticks)-1):
        xticks.insert((i+1)*2-1,'')
    xaxis.ticktext = xticks
    xaxis.tickvals = list(range(len(xticks)))
    xaxis.range = [-0.5,len(xticks)-1]

    div = plot(data, show_link=False, validate=False,output_type='div', include_plotlyjs=False)
    id = str(div[9:div.index('"',9)])
    with open('plot.html','w') as f:
        f.write('<html><head><meta charset="utf-8" /></head><body><script src="plotly.js"></script>')
        f.write(div)
        script = """
            <script type="text/javascript">
            window.removeEventListener("resize");window.addEventListener("resize", function(){Plotly.Plots.resize(document.getElementById("%s"));});
            var last_update = %i;
            var now = Math.floor(Date.now() / 1000);
            var refresh = 10;
            if (now-last_update < 55)
                refresh = last_update+65 - now;

            setTimeout(function() {location.reload()}, refresh * 1000);
            </script>
         """
        f.write(script % (id, floor(time())))
