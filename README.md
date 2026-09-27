# FeedFlow RSS — Notícias da TV

RSS próprio para a editoria Televisão do Notícias da TV.

- Atualização programada a cada 5 minutos pelo GitHub Actions.
- A data/hora vem da página individual da matéria, em `.artigo-data`.
- Na primeira execução, somente a matéria mais recente entra no RSS; as demais já existentes são marcadas como conhecidas para evitar uma carga inicial de notícias antigas.
- Se a data original não puder ser obtida, a matéria não é publicada com o horário da sincronização.

## GitHub Pages

Depois de enviar os arquivos ao repositório `willianrss/feeddowillian`:

1. **Settings → Pages**
2. **Deploy from a branch**
3. Branch: **main**
4. Folder: **/docs**
5. **Save**

RSS: `https://willianrss.github.io/feeddowillian/noticiasdatv-televisao.xml`
