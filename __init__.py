# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from os.path import dirname, join

import simplematch
import wikipedia_for_humans
from ovos_classifiers.heuristics.keyword_extraction import HeuristicExtractor
from ovos_plugin_manager.templates.solvers import QuestionSolver
from ovos_utils import classproperty
from ovos_utils.intents import IntentBuilder
from ovos_utils.gui import can_use_gui
from ovos_utils.process_utils import RuntimeRequirements
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills.common_query_skill import CommonQuerySkill, CQSMatchLevel


class WikipediaSolver(QuestionSolver):
    priority = 40
    enable_tx = True

    def __init__(self, config=None):
        config = config or {}
        config["lang"] = "en"  # only supports english
        super().__init__(config)
        self.cache.clear()

    def extract_keyword(self, query, lang="en"):
        # TODO - from mycroft.conf
        keyword_extractor = HeuristicExtractor()
        return keyword_extractor.extract_subject(query, lang)

    def get_secondary_search(self, query, lang="en"):
        if lang == "en":
            match = simplematch.match("what is the {subquery} of {query}", query)
            if match:
                return match["query"], match["subquery"]
        query = self.extract_keyword(query, lang)
        return query, None

    def extract_and_search(self, query, context=None):
        context = context or {}
        lang = context.get("lang") or self.default_lang
        lang = lang.split("-")[0]

        # extract the best keyword
        query = self.extract_keyword(query, lang)
        if not query:
            return {}
        return self.search(query, context)

    # officially exported Solver methods
    def get_data(self, query, context=None):
        """
       query assured to be in self.default_lang
       return a dict response
       """
        context = context or {}
        lang = context.get("lang") or self.default_lang
        lang = lang.split("-")[0]

        page_data = wikipedia_for_humans.page_data(query, lang=lang) or {}
        data = {
            "short_answer": wikipedia_for_humans.tldr(query, lang=lang),
            "summary": wikipedia_for_humans.summary(query, lang=lang)
        }
        if not page_data:
            query, subquery = self.get_secondary_search(query, lang)
            if subquery:
                data = {
                    "short_answer": wikipedia_for_humans.tldr_about(subquery, query, lang=lang),
                    "summary": wikipedia_for_humans.ask_about(subquery, query, lang=lang)
                }
            else:
                data = {
                    "short_answer": wikipedia_for_humans.tldr(query, lang=lang),
                    "summary": wikipedia_for_humans.summary(query, lang=lang)
                }
        page_data.update(data)
        page_data["title"] = page_data.get("title") or query
        return page_data

    def get_spoken_answer(self, query, context=None):
        data = self.extract_and_search(query, context)
        return data.get("summary", "")

    def get_image(self, query, context=None):
        """
        query assured to be in self.default_lang
        return path/url to a single image to acompany spoken_answer
        """
        data = self.extract_and_search(query, context)
        try:
            return data["images"][0]
        except:
            return None

    def get_expanded_answer(self, query, context=None):
        """
        query assured to be in self.default_lang
        return a list of ordered steps to expand the answer, eg, "tell me more"

        {
            "title": "optional",
            "summary": "speak this",
            "img": "optional/path/or/url
        }

        """
        data = self.get_data(query, context)
        img = self.get_image(query, context)
        steps = [{
            "title": data.get("title", query).title(),
            "summary": s,
            "img": img
        }
            for s in self.sentence_split(data["summary"], -1)]
        for sec in data.get("sections", []):
            steps += [{
                "title": sec.get("title", query).title(),
                "summary": s,
                "img": img
            }
                for s in self.sentence_split(sec["text"], -1)]
        return steps


class WikipediaSkill(CommonQuerySkill):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "lang" in self.settings:
            lang = self.settings["lang"]
        else:
            lang = self.lang.split("-")[0]
        self.wiki = WikipediaSolver(config={"lang": lang})

        # for usage in tell me more / follow up questions
        self.idx = 0
        self.results = []
        self.image = None

    @classproperty
    def runtime_requirements(self):
        return RuntimeRequirements(
            internet_before_load=True,
            network_before_load=True,
            gui_before_load=False,
            requires_internet=True,
            requires_network=True,
            requires_gui=False,
            no_internet_fallback=False,
            no_network_fallback=False,
            no_gui_fallback=True,
        )

    # intents
    @intent_handler("wiki.intent")
    def handle_search(self, message):
        """Extract what the user asked about and reply with info
        from wikipedia.
        """
        self.gui.show_animated_image(join(dirname(__file__), "ui", "jumping.gif"))
        self.current_title = query = message.data["query"]
        self.speak_dialog("searching", {"query": query})
        self.image = None
        title, summary = self.ask_the_wiki(query)
        if summary:
            self.speak_result()
        else:
            self.speak_dialog("no_answer")

    # @intent_handler("wikiroulette.intent")
    def handle_wiki_roulette_query(self, message):
        """Random wikipedia page"""
        self.gui.show_animated_image(join(dirname(__file__), "ui", "jumping.gif"))
        self.image = None
        self.current_title = "Wiki Roulette"
        self.speak_dialog("wikiroulette")
        # TODO

    @intent_handler(IntentBuilder("WikiMore").require("More").require("wiki_article"))
    def handle_tell_more(self, message):
        """Follow up query handler, "tell me more".

        If a "spoken_lines" entry exists in the active contexts
        this can be triggered.
        """
        self.speak_result()

    # common query
    def CQS_match_query_phrase(self, phrase):
        title, summary = self.ask_the_wiki(phrase)
        if summary:
            self.idx += 1  # spoken by common query
            return (
                phrase,
                CQSMatchLevel.GENERAL,
                summary,
                {"query": phrase, "image": self.image, "title": title, "answer": summary},
            )

    def CQS_action(self, phrase, data):
        """If selected show gui"""
        self.display_wiki_entry()
        self.set_context("WikiKnows", data.get("title") or phrase)

    # wikipedia
    def ask_the_wiki(self, query):
        # context for follow up questions
        self.set_context("WikiKnows", query)
        self.idx = 0
        try:
            self.results = self.wiki.long_answer(query, lang=self.lang)
        except Exception as err:  # handle solver plugin failures, happens in some queries
            self.log.error(err)
            self.results = None

        self.image = self.wiki.get_image(query)
        if self.results:
            title = self.results[0].get("title") or query
            return title, self.results[0]["summary"]
        return None, None

    def display_wiki_entry(self, title="Wikipedia", image=None):
        if not can_use_gui(self.bus):
            return
        image = image or self.image
        if image:
            self.gui.show_image(image, title=title, fill=None, override_idle=20, override_animations=True)

    def speak_result(self):
        if self.idx + 1 > len(self.results):
            self.speak_dialog("thats all")
            self.remove_context("WikiKnows")
            self.idx = 0
        else:
            self.speak(self.results[self.idx]["summary"])
            self.set_context("WikiKnows", "wikipedia")
            self.display_wiki_entry(self.results[self.idx].get("title", "Wikipedia"))
            self.idx += 1

    def stop(self):
        self.gui.release()


if __name__ == "__main__":
    d = WikipediaSolver()

    query = "who is Isaac Newton"

    # full answer
    ans = d.spoken_answer(query)
    print(ans)
    # Sir Isaac Newton  (25 December 1642 – 20 March 1726/27) was an English mathematician, physicist, astronomer, alchemist, theologian, and author (described in his time as a "natural philosopher") widely recognised as one of the greatest mathematicians and physicists of all time and among the most influential scientists. He was a key figure in the philosophical revolution known as the Enlightenment. His book Philosophiæ Naturalis Principia Mathematica (Mathematical Principles of Natural Philosophy), first published in 1687, established classical mechanics. Newton also made seminal contributions to optics, and shares credit with German mathematician Gottfried Wilhelm Leibniz for developing infinitesimal calculus.
    # In the Principia, Newton formulated the laws of motion and universal gravitation that formed the dominant scientific viewpoint until it was superseded by the theory of relativity. Newton used his mathematical description of gravity to derive Kepler's laws of planetary motion, account for tides, the trajectories of comets, the precession of the equinoxes and other phenomena, eradicating doubt about the Solar System's heliocentricity. He demonstrated that the motion of objects on Earth and celestial bodies could be accounted for by the same principles. Newton's inference that the Earth is an oblate spheroid was later confirmed by the geodetic measurements of Maupertuis, La Condamine, and others, convincing most European scientists of the superiority of Newtonian mechanics over earlier systems.
    # Newton built the first practical reflecting telescope and developed a sophisticated theory of colour based on the observation that a prism separates white light into the colours of the visible spectrum. His work on light was collected in his highly influential book Opticks, published in 1704. He also formulated an empirical law of cooling, made the first theoretical calculation of the speed of sound, and introduced the notion of a Newtonian fluid. In addition to his work on calculus, as a mathematician Newton contributed to the study of power series, generalised the binomial theorem to non-integer exponents, developed a method for approximating the roots of a function, and classified most of the cubic plane curves.
    # Newton was a fellow of Trinity College and the second Lucasian Professor of Mathematics at the University of Cambridge. He was a devout but unorthodox Christian who privately rejected the doctrine of the Trinity. He refused to take holy orders in the Church of England unlike most members of the Cambridge faculty of the day. Beyond his work on the mathematical sciences, Newton dedicated much of his time to the study of alchemy and biblical chronology, but most of his work in those areas remained unpublished until long after his death. Politically and personally tied to the Whig party, Newton served two brief terms as Member of Parliament for the University of Cambridge, in 1689–1690 and 1701–1702. He was knighted by Queen Anne in 1705 and spent the last three decades of his life in London, serving as Warden (1696–1699) and Master (1699–1727) of the Royal Mint, as well as president of the Royal Society (1703–1727).

    # chunked answer, "tell me more"
    for sentence in d.long_answer(query):
        print(sentence["title"])
        print(sentence["summary"])
        print(sentence.get("img"))

        # who is Isaac Newton
        # Sir Isaac Newton  (25 December 1642 – 20 March 1726/27) was an English mathematician, physicist, astronomer, alchemist, theologian, and author (described in his time as a "natural philosopher") widely recognised as one of the greatest mathematicians and physicists of all time and among the most influential scientists.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # He was a key figure in the philosophical revolution known as the Enlightenment.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # His book Philosophiæ Naturalis Principia Mathematica (Mathematical Principles of Natural Philosophy), first published in 1687, established classical mechanics.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # Newton also made seminal contributions to optics, and shares credit with German mathematician Gottfried Wilhelm Leibniz for developing infinitesimal calculus.
        # In the Principia, Newton formulated the laws of motion and universal gravitation that formed the dominant scientific viewpoint until it was superseded by the theory of relativity.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # Newton used his mathematical description of gravity to derive Kepler's laws of planetary motion, account for tides, the trajectories of comets, the precession of the equinoxes and other phenomena, eradicating doubt about the Solar System's heliocentricity.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # He demonstrated that the motion of objects on Earth and celestial bodies could be accounted for by the same principles.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # Newton's inference that the Earth is an oblate spheroid was later confirmed by the geodetic measurements of Maupertuis, La Condamine, and others, convincing most European scientists of the superiority of Newtonian mechanics over earlier systems.
        # Newton built the first practical reflecting telescope and developed a sophisticated theory of colour based on the observation that a prism separates white light into the colours of the visible spectrum.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # His work on light was collected in his highly influential book Opticks, published in 1704.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # He also formulated an empirical law of cooling, made the first theoretical calculation of the speed of sound, and introduced the notion of a Newtonian fluid.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # In addition to his work on calculus, as a mathematician Newton contributed to the study of power series, generalised the binomial theorem to non-integer exponents, developed a method for approximating the roots of a function, and classified most of the cubic plane curves.
        # Newton was a fellow of Trinity College and the second Lucasian Professor of Mathematics at the University of Cambridge.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # He was a devout but unorthodox Christian who privately rejected the doctrine of the Trinity.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # He refused to take holy orders in the Church of England unlike most members of the Cambridge faculty of the day.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # Beyond his work on the mathematical sciences, Newton dedicated much of his time to the study of alchemy and biblical chronology, but most of his work in those areas remained unpublished until long after his death.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

        # who is Isaac Newton
        # Politically and personally tied to the Whig party, Newton served two brief terms as Member of Parliament for the University of Cambridge, in 1689–1690 and 1701–1702.
        # https://upload.wikimedia.org/wikipedia/commons/3/3b/Portrait_of_Sir_Isaac_Newton%2C_1689.jpg

    # bidirectional auto translate by passing lang context
    sentence = d.spoken_answer("Quem é Isaac Newton",
                               context={"lang": "pt"})
    print(sentence)
    # Sir Isaac Newton (25 de dezembro de 1642 - 20 de março de 1726/27) foi um matemático, físico, astrônomo, alquimista, teólogo e autor (descrito em seu tempo como um "filósofo natural") amplamente reconhecido como um dos maiores matemáticos e físicos de todos os tempos e entre os cientistas mais influentes. Ele era uma figura chave na revolução filosófica conhecida como Iluminismo. Seu livro Philosophiæ Naturalis Principia Mathematica (Princípios matemáticos da Filosofia Natural), publicado pela primeira vez em 1687, estabeleceu a mecânica clássica. Newton também fez contribuições seminais para a óptica, e compartilha crédito com o matemático alemão Gottfried Wilhelm Leibniz para desenvolver cálculo infinitesimal.
    # No Principia, Newton formulou as leis do movimento e da gravitação universal que formaram o ponto de vista científico dominante até ser superado pela teoria da relatividade. Newton usou sua descrição matemática da gravidade para derivar as leis de Kepler do movimento planetário, conta para as marés, as trajetórias dos cometas, a precessão dos equinócios e outros fenômenos, erradicando dúvidas sobre a heliocentricidade do Sistema Solar. Ele demonstrou que o movimento de objetos na Terra e corpos celestes poderia ser contabilizado pelos mesmos princípios. A inferência de Newton de que a Terra é um esferóide oblate foi mais tarde confirmada pelas medidas geodésicas de Maupertuis, La Condamine, e outros, convencendo a maioria dos cientistas europeus da superioridade da mecânica newtoniana sobre sistemas anteriores.
    # Newton construiu o primeiro telescópio reflexivo prático e desenvolveu uma teoria sofisticada da cor baseada na observação de que um prisma separa a luz branca nas cores do espectro visível. Seu trabalho sobre luz foi coletado em seu livro altamente influente Opticks, publicado em 1704. Ele também formulou uma lei empírica de resfriamento, fez o primeiro cálculo teórico da velocidade do som e introduziu a noção de um fluido Newtoniano. Além de seu trabalho em cálculo, como um matemático Newton contribuiu para o estudo da série de energia, generalizou o teorema binomial para expoentes não inteiros, desenvolveu um método para aproximar as raízes de uma função e classificou a maioria das curvas de plano cúbico.
    # Newton era um companheiro do Trinity College e o segundo professor Lucasian de Matemática na Universidade de Cambridge. Ele era um cristão devoto, mas não ortodoxo, que rejeitou privadamente a doutrina da Trindade. Ele se recusou a tomar ordens sagradas na Igreja da Inglaterra, ao contrário da maioria dos membros da faculdade de Cambridge do dia. Além de seu trabalho nas ciências matemáticas, Newton dedicou grande parte de seu tempo ao estudo da alquimia e da cronologia bíblica, mas a maioria de seu trabalho nessas áreas permaneceu inédita até muito tempo depois de sua morte. Politicamente e pessoalmente ligado ao partido Whig, Newton serviu dois mandatos breves como membro do Parlamento para a Universidade de Cambridge, em 1689-1690 e 1701-1702. Ele foi condecorado pela rainha Anne em 1705 e passou as últimas três décadas de sua vida em Londres, servindo como Warden (1696-1699) e Master (1699–1727) da Royal Mint, bem como presidente da Royal Society (1703–1727)
