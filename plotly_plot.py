from math import floor
from time import time

def init_plotly():
    import locale
    locale.setlocale(locale.LC_ALL,'de_DE')
    from sys import stdout
    stdout.write("init plotly..")
    global plot, Scatter, Layout, Bar, Xaxis, YAxis, Legend, Marker, Annotation
    global bar, scatter, layout, data, last_bar, day_line, day_marker
    from plotly.offline import plot
    from plotly.graph_objs import Scatter, Layout, Bar, XAxis, YAxis, Legend, Marker, Annotation

    # data
    bar = Bar(x=[], y=[], name='Aktueller Verbrauch')
    last_bar = Bar(x=[], y=[], name='Bisheriger Verbrauch<br>in aktueller Stunde', marker=dict(color='rgb(159, 197, 232)'))
    scatter = Scatter(x=[], y=[], name='Durchschnittlicher Verbrauch', mode="lines+markers")

    #layout
    layout = Layout(
        title="Stromverbrauch der letzten 24 Stunden", barmode='stacked',
        xaxis= XAxis(tickmode='array', ticktext=[], tickvals=[], range=[-0.5,1], tickangle=-45),
        yaxis= YAxis(title='Wh'),
        legend=Legend(bordercolor='#FFFFFF', borderwidth=3, xanchor="right", x=1.0,y=1.11) # bgcolor='#E2E2E2'
    )

    data = {
        "data": [bar,scatter,last_bar],
        "layout": layout
    }
    print(" done")

def day_start(x,ts):
    x *= 2
    annotation = Annotation(xref='x',yref='paper', y=0.9, x=x, text=ts.strftime('%a, %d.%m'),showarrow=False,xanchor='left')
    line = {'type':'line','xref':'x','yref':'paper','y0':0,'y1':1,'x0':x,'x1':x,'line':{'width':1}}
    return line, annotation


def plot_plotly(current_consumption, avg_consumption, xticks, day_starts):
    bar.x = list(range(1,len(current_consumption)*2-2,2))
    bar.y = current_consumption[:-1]
    last_bar.x = [len(current_consumption)*2-1]
    last_bar.y = [current_consumption[-1]]

    scatter.x = list(range(1,len(avg_consumption)*2,2))
    scatter.y = avg_consumption

    lines, annotations = [],[]
    for i in day_starts:
        line,annotation=day_start(*i)
        lines.append(line)
        annotations.append(annotation)
    layout['shapes'] = lines
    layout['annotations'] = annotations

    xaxis = layout.xaxis
    for i in range(len(xticks)-1):
        xticks.insert((i+1)*2-1,'')
    xaxis.ticktext = xticks
    xaxis.tickvals = list(range(len(xticks)))
    xaxis.range = [len(xticks)-49,len(xticks)-1]

    div = plot(data, show_link=False, validate=False,output_type='div', include_plotlyjs=False)
    id = str(div[9:div.index('"',9)])
    with open('html/plot.html','w') as f:
        header = """
            <html><head><meta charset="utf-8" /></head><body>
            <script src="plotly.min.js"></script>
            <script src="jquery.min.js"></script>
            <div style="
                position: absolute;
                z-index: 1;
                font-family: 'Open Sans', verdana, arial, sans-serif;
                font-size: 11px;">
                <input id="auto_refresh" type="checkbox" checked>Automatische Aktualisierung
            </div>
        """
        f.write(header)
        f.write('')
        f.write(div)
        script = """
            <script type="text/javascript">
            function setup_refresh(first_time) {
                var last_update = %i;
                var now = Math.floor(Date.now() / 1000);
                var refresh = first_time ? 10 : 0;
                if (now-last_update < 55)
                    refresh = last_update+65 - now;
                setTimeout(function() {
                    if ($('#auto_refresh').is(":checked"))
                        location.reload()
                }, refresh * 1000);
            }
            setup_refresh(true);
            $(function() {
                $('#auto_refresh').change(function() {
                    if (this.checked)
                        setup_refresh(false);
                });

                window.addEventListener("resize", function(){Plotly.Plots.resize(document.getElementById("%s"));});
            });
            </script>
         """
        f.write(script % (floor(time()), id))
