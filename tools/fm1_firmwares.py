#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The FM-1 firmwares FM-1 Switch can name: The Firmware Hub's list (its catalogue, CC BY 4.0, see
CATALOG_*), plus what this app itself knows about a few of them (KNOWN). It tells which firmware
an FM-1 runs from the identities ("FM-1_918") its app carries; what nobody has checked is "unknown",
never guessed.
  python tools/fm1_firmwares.py FM-1_918           which firmware that identity is
  python tools/fm1_firmwares.py --update catalog.json
                                                   rewrite the list from a downloaded catalogue
                                                   (https://thefirmwarehub.com/catalog.json, read at most
                                                   once a day, as the hub asks)"""
import json
import re
import sys
from pathlib import Path

CATALOG_URL = "https://thefirmwarehub.com/catalog.json"
CATALOG_CREDIT = "Firmware list: The Firmware Hub – https://thefirmwarehub.com (CC BY 4.0)"
CATALOG_LICENSE = "CC-BY-4.0"
FIELDS = ("id", "name", "author", "repo", "ident", "product", "chip", "base")
CHIP_TEXT = {"new": "saves on newer FM-1s (UCUN flash, B36014) too", "old": "made for the first FM-1s (flash 856014)",
             "any": "saves on both flash chips"}
UNKNOWN = "unknown"

# What this app knows first hand, per firmware (the hub's id, or our own name): the update-mode soft key
# over MIDI (F0 22 24 35 7D F7), the keys held for update mode when that fails, and whether it starts
# under m-bootloader. Anything not listed, or not yet tried, stays UNKNOWN.
KNOWN = {
    "stock": {"name": "M-VAVE FM-1 (stock)", "soft_key": True, "keys": UNKNOWN, "m_bootloader": UNKNOWN},
    "felucca": {"soft_key": True, "keys": UNKNOWN, "m_bootloader": UNKNOWN},
    "wild": {"name": "Wild", "soft_key": True, "keys": "OCT- and OCT+ held for 5 seconds", "m_bootloader": UNKNOWN},
    "nb303": {"name": "NB303", "soft_key": True, "keys": "OCT- and OCT+ held for 5 seconds", "m_bootloader": UNKNOWN},
}

_BEGIN, _END = "# --- catalogue " + "begin ---", "# --- catalogue " + "end ---"

# The Firmware Hub's list (https://thefirmwarehub.com, CC BY 4.0), one firmware per line: FIELDS,
# tab-separated. Rewritten by --update; edit nothing between the two marks by hand.
# --- catalogue begin ---
CATALOG_UPDATED = "2026-11-23 09:02 UTC"
CATALOG = """\
zp12	zp12	zvenson	zvenson/zp12	FM-1_97[0-689]	FM-1_975	old	
hortator	Hortator	DEADACTIVE	deadactive/hortator	FM-1_\\d{3,8}	FM-1_900	new	
hortator-bt	Hortator (Bluetooth)	DEADACTIVE	deadactive/hortator	FM-1_\\d{3,8}	FM-1_900	new	
felucca	Felucca	Leo Kuroshita (Hügelton Instruments)	hugelton/Felucca	FM-1_91\\d	FM-1_918	new	
salt	Felucca [Salt]	Chance Roth (ChanceTheMaker)	ChanceTheMaker/Felucca	FM-1_\\d{3,8}	FM-1_909	old	
jangada	Jangada	zednaked	zednaked/jangada	FM-1_\\d{3,8}	FM-1_910	old	
ghoulbox	GHOULBOX	Jason Persinger	jasonpersinger/ghoulbox-fm1-dungeon-synth	FM-1_\\d{3,8}	FM-1_900	old	
melodee	Melodee	keremimo	keremimo/melodee	FM-1_\\d{3,8}	FM-1_91061	new	
sloop	SLOOP	isod89	isod89/sloop-fm1	FM-1_900	FM-1_900	new	
clipper	Clipper	isod89	isod89/clipper-fm1	FM-1_900	FM-1_900	new	sloop
bubba	Bubba Machine	Erbubar23	Erbubar23/bubba-machine	FM-1_003	FM-1_003	any	
floyd	SLOOP Floyd FM	René Bohne	renebohne/sloop-fm1	FM-1_\\d{3,8}	FM-1_924	old	
sloopbanks	SLOOP DX7 Banks	majnikool	majnikool/sloop-fm1	FM-1_\\d{3,8}	FM-1_925	old	
sloopdx	sloopDX	zvenson	zvenson/dxsloop	FM-1_93[0-689]|FM-1_940	FM-1_940	new	
x0x	X0X	Charles Vestal	charlesvestal/fm1-x0x	FM-1_90\\d{5}	FM-1_9010300	new	
x0xrmx	X0X-RMX	Mr-Roboto (Oliver)	Mr-Roboto/fm1-x0x-rmx	FM-1_90\\d{5}|FM-1_987\\d{6}	FM-1_987010100	old	x0x
fomni	FoMni	Charles Vestal	charlesvestal/fm1-fomni	FM-1_800\\d{4}	FM-1_8000500	new	
choralroot	ChoralRoot	Quixotic7	Quixotic7/ChoralRootFM1	FM-1_\\d{3,8}	FM-1_920	new	
waveloop	WaveLoop FM-1	ELI7VH	ELI7VH/Felucca	FM-1_\\d{3,8}	FM-1_910	old	
obervave	OberVave	zvenson	zvenson/obervave	FM-1_95[3-9]\\d	FM-1_9535	new	dinghy
shineon	Shine On	zvenson	zvenson/shineon	FM-1_96[3-9]\\d	FM-1_9635	new	obervave
dinghy	Dinghy	zvenson	zvenson/dinghy	FM-1_99[0-8]	FM-1_995	new	
vibe-link	VIBE FM Link	zvenson	zvenson/vibe-link	FM-1_984\\d	FM-1_9844	new	dinghy
fimba	FiMba-1	jadamsowers	jadamsowers/fm1-fimba	FM-1_800\\d{4}	FM-1_8000700	new	
jiant	JIANT FM	juanjiant-bit	juanjiant-bit/JIANT	FM-1_\\d{3,8}	FM-1_909	new	
chord	fm1-chord	math0ne	math0ne/fm1-chord	FM-1_\\d{3,8}	FM-1_903	old	
fumi	FuMi-1	cartesive	cartesive/fumi-1	FM-1_701\\d{4}	FM-1_7010101	new	
maku	MAKU	okumurari	okumurari2/maku	FM-1_\\d{3,8}	FM-1_910	old	
rainbow	Rainbow Mode	joshbartlettband-web	joshbartlettband-web/felucca-rainbow	FM-1_\\d{3,8}	FM-1_911	new	
amb	AMB-1	Charles Vestal	charlesvestal/fm1-amb	FM-1_\\d{3,8}	FM-1_7010302	new	
ctl	CTL-1	Charles Vestal	charlesvestal/fm1-ctl	FM-1_60[01]0\\d{3}	FM-1_6010003	new	
winampfm	winampFM Synth	Hericdk	hericdk/winampFM	FM-1_900	FM-1_900	old	sloop
mycelium	MYCELIUM	Mandalars	megagott/mycelium-fm1	FM-1_900	FM-1_900	old	felucca+sloop
polly	Polly	Charles Vestal	charlesvestal/fm1-polly	FM-1_3\\d{6}	FM-1_3000604	new	
etch	ETCH	Joppe Sikken (SikkenWorks)	SikkenWorks/ETCH	FM-1_\\d{3,8}	FM-1_1500	any	
fm106	FM-106	PatttF	PatttF/fm-106	FM-1_\\d{3,8}	FM-1_907	new	dinghy
tribe	TRIBE	apiel	apiel/fm1-tribe	FM-1_\\d{3,8}	FM-1_9000300	old	x0x
duell	DUELL	OSCILL/R	oscillr/DUELL	FM-1_\\d{3,8}	FM-1_901	new	
skiff	Skiff	illuminus5	illuminus5/skiff	FM-1_7(1[0-4]|2\\d)	FM-1_727	new	felucca
fart	Fart Machine One	adamcharleswilsonmusic-code	adamcharleswilsonmusic-code/Fart-Machine-One	FM-1_703	FM-1_703	old	
orbit	ORBIT	dspaudio	dspaudio/orbit	FM-1_\\d{3,8}	FM-1_900	old	sloop
felucca8	Felucca 8 Patterns	echoe	echoe/Felucca_8_patterns	FM-1_\\d{3,8}	FM-1_050	new	
pigeon	PIGEON	The Shrimp Blimp	TheShrimpBlimp/pigeon	FM-1_900	FM-1_900	old	felucca
cs1	CS-1	twochannels	twochannels/CS-1	FM-1_950	FM-1_950	old	dinghy
hyp1	HYP-1	RedtailWorks	RustyPawz/fm1-HYP1	FM-1_701\\d{4}	FM-1_7010570	new	amb
tonitor	Tonitor	Nuno Zimas	nzimas/tonitor	FM-1_700	FM-1_700	new	
bocjuhn	bocjuhn	ryuji kanagawa	IanP06/bocjuhn	FM-1_197100	FM-1_197100	old	tonitor
aisling	AISLING	CU_STE	stephencummins/fm1-aisling	FM-1_353\\d{6}	FM-1_353010601	new	nachtfahrt
demake	Demake	Sequencist	InigoHernandez/demake-synth	FM-1_969	FM-1_969	new	
bigorna	Bigorna	Nuno Zimas	nzimas/bigorna	(?:FM-1_808[08])|FM-1_8109	FM-1_8109	new	tonitor
noizdemons	NOIZ DEMONS	SPIKE the Percussionist	SPIKE-the-Percussionist/noiz_demons	FM-1_900	FM-1_900	old	
coralflower	CoralFlower	FicusFicusov	FicusFicusov/CoralFlower	FM-1_930	FM-1_930	new	
vector37	Vector37	genkigenki	genkigenki/Vector37	FM-1_\\d{3,8}	FM-1_700	old	
mc1	MC-1	dspaudio	dspaudio/mc-1	FM-1_900	FM-1_900	old	
beetle	BEETLE	Joppe Sikken (SikkenWorks)	SikkenWorks/BEETLE	FM-1_21\\d\\d	FM-1_2123	new	
min1	MIN-1	Reytian	Reytian/fm1-min1	FM-1_5000201	FM-1_5000201	old	
purplemonkey	PurpleMonkey	Quixotic7	Quixotic7/PurpleMonkeyFM1	FM-1_927	FM-1_927	new	
gammawave	Pocket GammaWave	ShadowTune (GammaWave Audio)	fvalente/GammaWave-FM1	FM-1_981	FM-1_981	old	
tuna	Tuna	delirioux	delirioux/tuna	(?:FM-1_900)|FM-1_47886200	FM-1_47886200	new	
frodhes	Frodhes	Leonardo Mirabal	leonardoMirabal/frodhes	FM-1_958	FM-1_958	new	dinghy
fomajam	FOMAJAM	konsumer	konsumer/fomajam	FM-1_961	FM-1_961	new	
kawa	KAWA	Omnipotard	Guizmoh/Kawa-Tracker-Fm1	FM-1_95[57]	FM-1_957	new	dinghy
nmsfm	No Man Sky FM	Hericdk	hericdk/nomanskyFM	FM-1_900	FM-1_900	old	sloop
fm1quest	FM1 Quest	Hericdk	hericdk/fm1-quest	FM-1_900	FM-1_900	old	sloop
grimm	GR!MM	GR!MM	GRiMMBIENT/grimm	FM-1_966	FM-1_966	old	dinghy
cutador	Cut-A-Dor	Kid Bunka	kidbee85-beep/cut-a-dor-release	FM-1_7010\\d{3}	FM-1_7010201	old	
mutable	Mutable Dreams	Kid Bunka	kidbee85-beep/mutable-dreams	(?:FM-1_71001\\d{4})|FM-1_710020300	FM-1_710020300	new	
fm1gm	FM1-GM	jartificer	jartificer/fm1-gm	FM-1_600	FM-1_600	new	
386dx	386DX	seajaysec	seajaysec/386-DX-FM1-KaraokeBot	FM-1_386\\d{6}	FM-1_386020000	old	
bunker	Bunker Evolved	Kid Bunka	kidbee85-beep/bunker-evolved	FM-1_7100\\d{3}	FM-1_7100002	old	felucca
opn1	OPN-1	putchdev	putchdev/opn-1	FM-1_950	FM-1_950	new	dinghy
sid1	SID-1	putchdev	putchdev/sid-1	FM-1_960	FM-1_960	new	dinghy
minitone	Mini-Tone	Stéphane Schmitz	eldontyrellcorp/Mini-Tone	FM-1_900	FM-1_900	old	felucca
morpheus	MORPHEUS	Ben Badio	benbadio/morpheus-fm1	FM-1_800\\d{4}	FM-1_8000013	old	dinghy
fmini	FMini-1	Ruppert	ruppert/fmini-1	FM-1_900	FM-1_900	new	felucca
dub	DUB-1	sbegueria	sbegueria/fm1-dub	FM-1_8010\\d{3}	FM-1_8010403	old	
fmel	FMEL-1	Antonio Altea	AntonioAltea/fmel-1	FM-1_95[0-3]	FM-1_953	new	dinghy
zen	ZEN-1	aceyprime	aceyprime/zen-1	FM-1_6010\\d{3}	FM-1_6010200	old	
golana	Golana	Gadlele	Gadlele/Golana	FM-1_91\\d	FM-1_913	old	
golanadz	Golana DZ Edition	dzwerg	dzwerg/Golana-DZ	FM-1_900	FM-1_900	old	
modelar	MODELAR	Hirakos	hirakos999/MODELAR	FM-1_42\\d{6}	FM-1_42000300	old	
niagara	Niagara Drift	Evan Yerburgh	evanyerburgh/niagara-drift	FM-1_8000\\d{3}	FM-1_8000301	new	
unholy	Unholy Union	actuallysparky	actuallysparky/FM-1-Unholy-Union-generative-drone-firmware	FM-1_8002\\d	FM-1_80023	old	
emb	Evander's Music Box	Michail Tsakiroglou	MikeTsak/Evanders-Music-Box--FM-1-LARP-firmware	FM-1_777	FM-1_777	old	
chipbox	Chipbox	pbarilla	pbarilla/chipbox	FM-1_337	FM-1_337	new	dinghy
loom	LOOM-8	Charles Vestal	charlesvestal/fm1-loom	FM-1_1\\d{7}	FM-1_10001802	new	
monk	MONK-1	Charles Vestal	charlesvestal/fm1-monk	(?:FM-1_5(?!000201)\\d{6})|FM-1_45001000	FM-1_45001000	new	
flyfish	Flyfish	delirioux	delirioux/flyfish	FM-1_900	FM-1_900	new	
bytemania	Bytemania	lloyd (llozd)	llozd/bytemania	FM-1_94[1-9]	FM-1_943	new	dinghy
hera	HERA	Ben Badio	benbadio/hera-fm1	FM-1_431\\d{4}	FM-1_4310100	old	dinghy
frasil	FRASIL	Styves Goupil	styvesgoupil/frasil	FM-1_47[0-4]\\d{5}	FM-1_47030400	new	
tessera	Tessera	Theo Dainis (Rastapopolix)	Rastapopolix/tessera	(?:FM-1_8377\\d{4})|FM-1_837	FM-1_837	new	
delrio	Del Rio	Jess Del Rio	jsopdebeeck/fm1-delrio	FM-1_48\\d{6}	FM-1_48010001	old	
carnac	Carnac OS	OrbVorgis	OwenKirby/carnac-os	FM-1_900	FM-1_900	old	
drift	DRIFT	FBNFRTG	FBNFRTG/drift-fm1	FM-1_983	FM-1_983	old	felucca
sidou	SIDOU	Denis Sablić	dsablic/sidou	FM-1_964	FM-1_964	new	
mono2k	MONO2K	Iñigo Hernandez	InigoHernandez/mono2k	FM-1_980	FM-1_980	new	
caravel	Caravel	BonszaiMusic	bonszaimusic/caravel	FM-1_999|FM-1_903	FM-1_999	new	
midimon	MIDIMON	ignis32	ignis32/midimonitor_fm1	FM-1_88\\d{6}	FM-1_88000500	new	
makohai	Makohai	delirioux	delirioux/makohai	FM-1_(960|47625642)	FM-1_47625642	new	dinghy
estampie	ESTAMPIE	Drew Robinson	ocean/estampie	FM-1_378\\d{3}	FM-1_378012	new	felucca+ghoulbox
fizz	FIZZ	Quixotic7	Quixotic7/FIZZ	FM-1_46\\d{6}	FM-1_46001300	new	
matryoshka	Matryoshka	michael glaviano	glvno/matryoshka	FM-1_71\\d|FM-1_70\\d{5}	FM-1_7012500	new	
loomfm1	Loom FM-1	Co-oscillator	Co-oscillator/Loom-FM-1	FM-1_985	FM-1_985	old	
tastywaves	TastyWaves	marksweiss	marksweiss/tastywaves	FM-1_937	FM-1_937	old	dinghy
arctic	Arctic Scream	PolarPatch	PolarPatch/Arctic-scream-FM-1	FM-1_977	FM-1_977	new	dinghy
s1vave	S1Vave	Andrew GG	AndrewGG88/S1Vave	FM-1_95[02]	FM-1_950	new	dinghy
gator	GATOR	ivanvoid	npi3pak/fm1-gator	FM-1_906	FM-1_906	old	
monohull	Monohull	Mark White	markterm/monohull	FM-1_928	FM-1_928	old	dinghy
perf1	PERF-1	deamonSLINKY	deamonSLINKY/PERF-1	FM-1_700\\d{4}	FM-1_7001700	new	
j6	J6	JackieAlacrity2373	JackieAlacrity2373/fm1-j6	FM-1_6601\\d{4}	FM-1_66010400	old	
guitchordi	GuitChordi	Stoogs	stoogs/guitchordi	FM-1_982	FM-1_982	old	
ravel	Ravel	De-Pattern	De-Pattern/Ravel_FM1	FM-1_820\\d{5}	FM-1_82014500	new	
berlinan	Berlinan	Jiří Píša	jiripisa/fm1-berlinan	FM-1_23\\d{6}	FM-1_23000501	old	
mnm1	MNM-1	Veff	galfaroth/mnm1	FM-1_61000100	FM-1_61000100	old	hortator
mope1	MOPE-1	slothpallas	slothpallas/mope-1	FM-1_6673\\d{6}	FM-1_6673000500	old	
kitten	Kitten Synths	Black Kitten Devices	BlackKittenDevices/Kitten-Synths	FM-1_969	FM-1_969	new	
nauclerus	Nauclerus	Mr. Roboto	Mr-Roboto/nauclerus	FM-1_25000	FM-1_25000	new	hortator
ptfm	PT/FM	hitosu	hitosu/ptfm	FM-1_7101	FM-1_7101	old	
spampy	SPAMPY	Spampy-Droid	Spampy-Droid/SPAMPY-0.0-r949	(?:FM-1_(?:949|036))|FM-1_058	FM-1_058	new	
spellbox	SPELLBOX	Philippe Jaccoud	pjac1810/spellbox	FM-1_57\\d	FM-1_573	old	
dungeontracker	Dungeon Tracker	Bourbon Weather	BourbonWX/Dungeon-Tracker	FM-1_91[05]	FM-1_915	old	
nachtfahrt	NACHTFAHRT	Cranky Man	FartyPants/fm1-nachtfahrt	FM-1_197\\d{6}	FM-1_197010002	new	
daurade	Daurade	delirioux	delirioux/daurade	FM-1_900	FM-1_900	new	
luxr	LUXR	HOLODECK AUDIOTOOLS	orbitator/LUXR	FM-1_870	FM-1_870	old	
fmdj	FMDj	DEADACTIVE	DeadActive/FMDj	FM-1_900	FM-1_900	old	felucca
kwasub	K-WASUB	Omnipotard (Guizmoh)	Guizmoh/K-WASUB_Firmware_for_FM1	FM-1_1990	FM-1_1990	new	
sill	Sill	delirioux	delirioux/sill	(?:FM-1_900)|FM-1_47745500	FM-1_47745500	new	felucca+tuna
smileyface	Smileyface	pbarilla	pbarilla/smileyface	FM-1_303	FM-1_303	new	chipbox
vesper	VESPER	Fat Cat Studios	fatcatstudios-hub/vesper-fm1	FM-1_8837\\d+	FM-1_8837202	new	
penumbra	Penumbra	Lunar Eclipse Forge	MadMooneyham/Penumbra	FM-1_73\\d{6}	FM-1_73010900	new	
jam	Jam Playground	Bob Perry	BunnyOrchard/jam-playground	FM-1_7666200	FM-1_7666200	old	
xmod	XMOD	Kot Morderca	KotMorderca/XMOD	FM-1_666	FM-1_666	new	
sidj	SiDJ	thomasj (tubesockor)	thomasj/fm1-sidj	FM-1_64\\d{6}	FM-1_64010501	new	
hawkchua	hawk-chua	plieuwski	Plieuw/hawk-chua	FM-1_007	FM-1_007	new	
bb1	BB1	Big Beat Studio	TexasRADIO/bb1-firmware	FM-1_922\\d{5}	FM-1_92200203	new	
karma	KARMA	Ariel Muchnik	eatyourgreensstudio-dotcom/karma-fm1	FM-1_926	FM-1_926	new	
lsdjburrito	LSDJ Burrito	Dreamy_bacon	dreamsofbacon/lsdj-burrito-src	FM-1_537\\d{6}	FM-1_537000997	new	felucca
fm1dock	fm1-dock	Joakim Eriksson	joakimeriksson/fm1-dock	FM-1_921	FM-1_921	new	dinghy
scintilla	Scintilla	Joakim Eriksson	joakimeriksson/scintilla-fm1	FM-1_963\\d{5}	FM-1_96300302	new	dinghy
fm1sid	FM-1 SID	Joakim Eriksson	joakimeriksson/fm1-sid	FM-1_900	FM-1_900	old	fm1dock
dark1	DARK-1	Polystellarflow	sparky480v/Dark-1	FM-1_320\\d{5}	FM-1_32010007	new	perf1
fm1trax	FM1-TRAX	jartificer	jartificer/fm1-trax	FM-1_601(\\d{6})?	FM-1_601	new	
wanderer	Wanderer	Nicholas James / Low Vision Music	spinnicholas/lvm-wanderer-fm1	FM-1_929	FM-1_929	old	dinghy
rupture	RUPTURE	Joppe Sikken (SikkenWorks)	SikkenWorks/RUPTURE	FM-1_968	FM-1_968	new	
pulhra	PULHRA	646timur-cell	646timur-cell/pulhra_main	FM-1_91\\d	FM-1_915	old	felucca
amenity	Amenity	Presethead	ejtb76/amenity	FM-1_817\\d{6}	FM-1_817020101	new	
arcturus	Arcturus S6	Zeptosecond DSP	zeptosecond-dsp/Arcturus-S6	FM-1_67686970	FM-1_67686970	old	dinghy
kittenrack	Kitten Rack	Black Kitten Devices	BlackKittenDevices/kitten-rack	FM-1_79\\d{3}	FM-1_79150	old	matryoshka
blop	Blop	Obsimo	OSR-Experiment/OSR-1	FM-1_9401\\d{4}	FM-1_94010100	old	
beetleblue	BEETLE BLUE	Joppe Sikken (SikkenWorks)	SikkenWorks/BEETLE-BLUE	FM-1_2124|FM-1_2224	FM-1_2224	new	beetle
b1	B1	Joppe Sikken (SikkenWorks)	SikkenWorks/B1-Drawbar-Organ	FM-1_481026	FM-1_481026	any	
clatter	CLATTER Pocket	allmyfriendsaresynths	clickysteve/clatter-pocket	FM-1_8133700	FM-1_8133700	any	
choppah	CHOPPAH	Laurens Profittlich	profikid/fm1-break-choppah	FM-1_86\\d{6}	FM-1_86010506	new	
veffbox	Veffbox	Veff	galfaroth/veffbox	FM-1_62000100	FM-1_62000100	old	
b3organ	B3 ORGAN	R-Jeb	R-Jeb/setBfree-fm1	FM-1_953	FM-1_953	new	dinghy
elixir	Elixir	Dan Majka	dmajka/elixir	FM-1_359\\d{6}	FM-1_359010501	new	felucca
dx747	DX747	Esben von Buchwald	esbenvb/fm1-dx747	FM-1_8747747	FM-1_8747747	any	
atlatl	Skiff Atlatl	LIkst4	LIkst4/skiff-atlatl	FM-1_742[1-9]\\d	FM-1_74212	new	skiff
coqui	COQUÍ	saulofilho	saulofilho/coqui-fm1	FM-1_923	FM-1_923	new	grimm
artihive	ARTIHIVE	KHAV	KHAVld/ARTIHIVE	FM-1_9620	FM-1_9620	new	dinghy
cultbox	CULTBOX	beatcult	beatcult/CULTBOX	FM-1_333	FM-1_333	new	dinghy
piterfly	Piterfly	Piotr Woźniak	piotrwozniak/piterfly	FM-1_112\\d{4}	FM-1_1120101	new	felucca
sardine	Sardine	delirioux	delirioux/sardine	FM-1_47727346	FM-1_47727346	new	
atek303	ATEK303	Animatek	animatek/FM1-Animatek	FM-1_330\\d{6}	FM-1_330010400	new	
audiorouter	FM1 Audio Router	MAX RAVE	maxravedj/fm1-audio-router	FM-1_731\\d{6}	FM-1_731000601	old	
drumlab	Drumlab	pbarilla	pbarilla/drumlab	FM-1_032	FM-1_032	new	chipbox
mvance	M-Vance	Kris (bk40-home)	bk40-home/M-vance	FM-1_9680\\d\\d	FM-1_968088	new	dinghy
patchwerk	Patchwerk	Matt Kuebrich	MattKuebrich/patchwerk	FM-1_606\\d*	FM-1_60600	new	
remotefm1	RemoteFM1	Quixotic7	Quixotic7/RemoteFM1	FM-1_5200\\d{4}|FM-1_72\\d{6}	FM-1_72000400	new	
rhythmer	Rhythmer	Jazzy Rhythmer	JazzyRhythmer/Rhythmer	FM-1_748\\d{3}	FM-1_748005	new	jiant
mikufm	Miku-FM	Michael Perry	MichaelAPerry/MIKU-FM1	FM-1_390\\d{5}	FM-1_39000900	new	dinghy
echofield	ECHOFIELD	PhilipsKnobs	Philip-Bos/echofield-fm1	FM-1_3246\\d{6}|FM-1_324	FM-1_324	new	
ombak	Ombak	Lunar Eclipse Forge	MadMooneyham/Ombak	FM-1_7401\\d{4}	FM-1_74010200	new	penumbra
xenolith	XENOLITH	Unit_v743	CaptainChronosis/XENOLITH	FM-1_29000	FM-1_29000	new	
hungrytravis	Hungry Travis	Trust This Mechanism	adamcharleswilsonmusic-code/Hungry-Travis	FM-1_797	FM-1_797	old	fomni
fmfam	FM-FAM	David Amo	davidamomusic/fm-fam	FM-1_111	FM-1_111	new	
jamtrack	JAMTRACK	PhilipsKnobs	Philip-Bos/jamtrack-fm1	FM-1_5268\\d{6}	FM-1_5268000203	new	
quartet	Quartet	MammothCheese	MammothCheese/quartet	FM-1_988	FM-1_988	new	dinghy
keystepone	keySTEP ONE	dzwerg	dzwerg/keySTEP-ONE	FM-1_7400\\d{4}	FM-1_74000542	old	
rambeaver	RAMBEAVER	Trust This Mechanism	adamcharleswilsonmusic-code/RAMBEAVER	FM-1_745	FM-1_745	old	
mpyre	M-PYRE	History Of Sky	HistoryOfSky/m-pyre	FM-1_123	FM-1_123	new	
assid	ASS-ID	altessproduction	altessproduction/ASS-ID_FM1	FM-1_234010000	FM-1_234010000	new	
sloopdub	sloop-DUB	altessproduction	altessproduction/sloop-dub-fm1	FM-1_234	FM-1_234	new	
fm1amy	fm1-amy	Dan Ellis	dpwe/fm1-amy	FM-1_26900\\d{4}	FM-1_269000100	new	
satie1	SATIE-1	OCKM Studio	OCKM-STUDIO/SATIE-1	FM-1_9980100	FM-1_9980100	new	
guru600	Guru-600	Marc Massé	MarcGoudy/guru-600	FM-1_600600	FM-1_600600	new	felucca
playa	PLAYA	JFS	juan-ms2lab/playa	FM-1_780	FM-1_780	new	
trumpagotchi	Trumpagotchi	Trust This Mechanism	adamcharleswilsonmusic-code/Trumpagotchi	FM-1_878670101	FM-1_878670101	old	fomni
krisjangle	Kris Jangle	w33tSnx	doohinkus/kris-jangle	FM-1_2000\\d{4}	FM-1_20001500	new	
cord	CORD	Chase DeAnda	chasedeanda/cord-fm1	FM-1_26730001	FM-1_26730001	new	
pocketband	Pocket Band	elpabl0	elpabl0/pocketband	FM-1_722604	FM-1_722604	new	
mightyarp	Mighty Arp	Moby Pixel	NickCulbertson/FM1-Mighty-Arp	FM-1_6279\\d{4}	FM-1_62790105	new	
dubbox	DUB-BOX	altessproduction	altessproduction/DUB-BOX-FM1	FM-1_2345	FM-1_2345	new	
deepfield	DEEP FIELD	Kyle McCann	WisestWizard/deepfield	(?:FM-1_44010600)|FM-1_44011100	FM-1_44011100	new	
splitmilk	SPLITMILK	MammothCheese	MammothCheese/SPLITMILK	FM-1_989	FM-1_989	new	dinghy
argo	Argo	echoe	echoe/argo_fm1	FM-1_050	FM-1_050	new	
kriechstrom	KRIECHSTROM	Grigorii Bezborodov	Xforsa/kriechstrom	FM-1_5781\\d{4}	FM-1_57810002	new	
paperlagoon	PaperLagoon	hacha	hacha/paperlagoon	FM-1_1612\\d{6}	FM-1_1612010100	new	
tack	Tack	oikoaudio	oikoaudio/tack	FM-1_7500\\d{4}	FM-1_75001500	new	
manifold	Manifold	stets43	stets43/Manifold	FM-1_6301\\d{4}	FM-1_63010006	new	
loafbreaker	LOAF BREAKER	FLEXOR	FLEXOR-R/loaf-breaker	FM-1_262302\\d{4}	FM-1_2623020973	new	choppah
fm1midimania	FM-1 Midi-Mania	Shan-Sherif	shan-sherif/fm-1-midi-mania	(?:FM-1_137090)|FM-1_137094	FM-1_137094	new	
fm1yoga	FM1 YOGA	Michael Milz	michamilz/fm1-yoga	FM-1_432010000	FM-1_432010000	new	
hadal1	HADAL-1	Polystellarflow	sparky480v/hadal-1	(?:FM-1_96010000)|FM-1_96010001	FM-1_96010001	new	
agtdeck	AGTDECK	Kevin Elliott	kevinelliott/fm-1-agent-deck	FM-1_24356	FM-1_24356	new	dinghy
clavioline	Clavioline	MammothCheese	MammothCheese/clavioline	FM-1_987	FM-1_987	new	dinghy
rhytmsec1	RHYTM-SEC-1	James Hampshire	hamperj/rhytm-sec-1	FM-1_924	FM-1_924	new	fm1dock+perf1
rakt	RAKT	Chase DeAnda	chasedeanda/rakt-fm1	FM-1_57258001	FM-1_57258001	new	
lrry	L/RRY	OSCILL/R	oscillr/LRRY	FM-1_9177	FM-1_9177	new	
manta	Manta	delirioux	delirioux/manta	FM-1_47626820	FM-1_47626820	new	
adrift	Adrift	Romain Guy	romainguy/adrift	FM-1_46200\\d{4}	FM-1_462000400	new	
aislingfx1	AISLING FX-1	CU_STE	stephencummins/fx1-aisling	(?:FM-1_391010000)|FM-1_391010100	FM-1_391010100	new	
drumtonic	Drumtonic	Federico Ciapi	fc79/drumtonic	FM-1_987	FM-1_987	new	dinghy
arpwav09	ARP WAV 09	Wav.09.02	wav0902/wav-fm1	FM-1_9080	FM-1_9080	new	dinghy
golf	Golf	BARNIR	barnierg76/golf-fm1	FM-1_28010000	FM-1_28010000	new	
tekstuur	Tekstuur	BARNIR	barnierg76/tekstuur-fm1	FM-1_27020000	FM-1_27020000	new	
kurzhaar	Kurzhaar, der Basshund	JazzMasterT	kurzhaar168/kurzhaar	FM-1_9168\\d{4}	FM-1_91680101	new	dinghy
miniju08	Mini-Ju08	Stephane Schmitz	eldontyrellcorp/Mini-Ju08	(?:FM-1_900)|FM-1_800	FM-1_800	new	minitone
atlatltheatltuner	Atl-Atl: The Atltuner	Jason Scheirer	jasonbot/atl-atl	FM-1_922	FM-1_922	new	
minigrain	Mini-Grain	eldontyrellcorp	eldontyrellcorp/Mini-Grain	FM-1_963	FM-1_963	new	
mvsynth2	M-Vsynth2	Kris (bk40-home)	bk40-home/M-Vsynth2	(?:FM-1_9681002)|FM-1_9681005	FM-1_9681005	new	dinghy
trapstationos	TRAPSTATION OS	Schlensog Audio Lab	sog-maker/trapstation-os	FM-1_511	FM-1_511	new	
astra	Astra	Michael Perry	MichaelAPerry/ASTRA-FM-1	FM-1_44000100	FM-1_44000100	new	dinghy
glitch	glitch	saulofilho	saulofilho/glitch	FM-1_986	FM-1_986	new	
quartz	QUARTZ	f0nski	F0nski/quartz	(?:FM-1_98000500)|FM-1_98001000	FM-1_98001000	new	
fmav1	FMav-1	wootrop & gpix	wootr0p/fmav1	FM-1_3628	FM-1_3628	new	dinghy
juicy	Juicy	Daniel Gilbert	DanielGilbert/Juicy	FM-1_88600\\d{2}	FM-1_8860001	new	dinghy
fmlarynx	FMLarynx	Presethead	ejtb76/FMLarynx	FM-1_98400\\d{4}	FM-1_984000402	new	dinghy
fernbedienung	Fernbedienung	Kabelmaier	DPangerl/fernbedienung	(?:FM-1_1492410)|FM-1_1492412	FM-1_1492412	new	
beiwagen	Beiwagen	Kabelmaier	DPangerl/beiwagen	FM-1_1492302	FM-1_1492302	new	
fm1chimeravocal	FM-1 Chimera Vocal	Shan-Sherif	shan-sherif/fm-1-chimera-vocal	FM-1_04200\\d{4}	FM-1_042000900	new	
orgigs	OrGigs	Bobo	orgelbobo/orgigs	(?:FM-1_674102)|FM-1_674110	FM-1_674110	new	dinghy
dabke	DABKE	STUDIOMORPHIK	wissamsader/dabke	(?:FM-1_65001600)|FM-1_65001900	FM-1_65001900	new	
obssynthfm1	ObsSynth FM-1	Orian Beltrame	orianbsilva/obssynth-fm1	FM-1_627010	FM-1_627010	new	
evilgenius	EVIL GENIUS	Quixotic7	Quixotic7/evilgenius	FM-1_4500\\d{4}	FM-1_45000104	new	
nccnovationcircuitcompan	NCC – Novation Circuit Companion	Fenderberg	fenderberg/fm1-nc-companion	FM-1_707	FM-1_707	new	
bluesbox	Blues Box	Brian Provost	provostprovost/blues	(?:FM-1_615012)|FM-1_615022	FM-1_615022	new	dinghy
conductor	CONDUCTOR	MammothCheese	MammothCheese/conductor	FM-1_1812	FM-1_1812	new	dinghy
aetherfm1	AETHER FM1	GR!MM	jeanlucmouton30-jpg/AETHER-FM1	FM-1_967	FM-1_967	new	dinghy
quantumgammawave	Quantum GammaWave	Guille Lauría	Guillel51/Quantum-GammaWave	FM-1_965	FM-1_965	new	
yehnah	Yehnah	Mello-D	Mello-D/yehnah	FM-1_069	FM-1_069	new	
cursedos	cursed os	cursedsound	cursedsound-music/CS_FM1	FM-1_420	FM-1_420	any	jiant
stallion	Stallion	Eberon Limited (John Gorman)	EberonLtd/stallion-fm1	(?:FM-1_104210)|FM-1_104211	FM-1_104211	new	
vnl1	VNL-1	pianomansam	pianomansam/fm1-vnl	FM-1_2200\\d{4}	FM-1_22000902	new	
moss1	MOSS-1	Theo Wecker	theowecker/fm1-moss	FM-1_44010000	FM-1_44010000	new	
ruido	Ruido	Miguel Hernández Domenech	miguelhd/ruido	FM-1_312	FM-1_312	new	
fmkick	FM-KICK	David Amo	davidamomusic/fm-kick	FM-1_222	FM-1_222	new	
ambpond	amb-pond	superxor	superxor/amb-pond	FM-1_8008	FM-1_8008	new	
morse	MORSE	Da-Wei Lee	daviddwlee84/fm1-morse	(?:FM-1_41000300)|FM-1_41000800	FM-1_41000800	new	
darkfunkdurcalsynt	Dark Funk Durcal Synt	Polaris Sound Lab	drophlex/dark-funk-durcal-synt-fm1	FM-1_904	FM-1_904	new	
andromeda	ANDROMEDA	Polaris Sound Lab	drophlex/ANDROMEDAFM1	FM-1_905	FM-1_905	new	
samsara	SAMSARA	Tomorrow's Man	tomorrowsman/fm1-samsara	FM-1_10801\\d{4}	FM-1_108010000	new	amb
fm1tonic	FM1 TONIC	Luigi Rossi	luigirossi-music/fm1-tonic	(?:FM-1_77010600)|FM-1_77010802	FM-1_77010802	new	
dp3oyb	DP3OYB	DP3OYB	DP3OYB/DP3OYB	FM-1_50509	FM-1_50509	new	
plissken	Plissken	OscarDad	dustekin/plissken	(?:FM-1_1981001100)|FM-1_1981003100	FM-1_1981003100	new	
sy1	SY1	Fenderberg	fenderberg/fm1-sy1	(?:FM-1_5100\\d{4})|FM-1_51010000	FM-1_51010000	new	
dayvx	DAYV-X	Repoman	halfman73/dayv-x	FM-1_93800\\d{4}	FM-1_938000101	new	felucca
zenith1	ZENITH-1	Polystellarflow	sparky480v/zenith-1	FM-1_97010000	FM-1_97010000	new	
u0u	U0U	@Y_oulisse	youlisse/U0U	FM-1_21000\\d{2}	FM-1_2100004	new	
h1	h1	Jake Handy	jakehandy/h1	FM-1_48100\\d{4}	FM-1_481000300	new	
fm1zx	FM-1 ZX	Orest Kulik	okulik/fm1-zx	FM-1_048	FM-1_048	new	
orbita	ORBITA	Polaris Sound Lab	drophlex/ORBITA	FM-1_908	FM-1_908	new	
textura	Textura	Leonardo Mirabal	leonardoMirabal/textura	FM-1_959	FM-1_959	new	dinghy
valle	Vallée	tammygucci	tammygucci/valee	FM-1_5609	FM-1_5609	new	skiff
dfdacidengine	DFD ACID ENGINE	Polaris Sound Lab	drophlex/DFD-ACID-ENGINE	FM-1_954	FM-1_954	new	
neon	NEON	Neon Summers	LotionOnTheSkin/NEON	FM-1_1984	FM-1_1984	new	
sq80	SQ-80	Leonardo Lopez Chavira	leonardolopezchavira/sq80-fm1	FM-1_8000\\d{4}	FM-1_80001400	new	
loopheads	LOOPHEADS	Jaak St-Jaaques	jaak-stjaaques/LOOPHEADS-public	FM-1_566741	FM-1_566741	new	dinghy
pingos1	PING OS-1	PING AUDIO	PINGAUDIO/PING-OS-1	FM-1_88801\\d{4}	FM-1_888010001	new	
metronome	METRONOME	Da-Wei Lee	daviddwlee84/fm1-metronome	(?:FM-1_59000300)|FM-1_59000400	FM-1_59000400	new	
castletunia	Castletunia	Brian Provost	provostprovost/castletunia	(?:FM-1_742010)|FM-1_742014	FM-1_742014	new	dinghy
tether	Tether	utopick	utopick/fm1-pocket	FM-1_9250	FM-1_9250	new	
clairpiano	Clair Piano	Simon Tokumine	tokumine/fm1-claire-piano	(?:FM-1_65010000)|FM-1_65010001	FM-1_65010001	new	
burgerstudiofm1	Burger Studio FM-1	Maker Hip (Hippie Maker)	perawoga2-coder/Fm1sampa	FM-1_4627	FM-1_4627	new	felucca
fm1800	FM1-800	morbious22	morbious22/FM1-800	FM-1_80086	FM-1_80086	new	felucca
viber	Viber	Anto	ant25fr/viber	FM-1_2507010000	FM-1_2507010000	new	felucca+skiff
sillage	Sillage	T-TAURI	lelacnoir-lab/Sillage	(?:FM-1_946220)|FM-1_946221	FM-1_946221	new	felucca+skiff
basssomething2	Bass Something 2	John Brown	imijes/bass-something-2	FM-1_6900\\d{4}	FM-1_69000904	new	
chordrun	CHORD RUN	swtdr34mz (based on Blop by Obsimo)	swtdr34mz-afk/CHORD_RUN	FM-1_571\\d{6}	FM-1_571020600	new	
volna	Volna	M_Bougrimoff	MaxMaximoff/volna	FM-1_3006	FM-1_3006	new	dinghy
insertcoin	INSERT COIN	swtdr34mz (based on Blop by Obsimo)	swtdr34mz-afk/INSERT_COIN	FM-1_573000800	FM-1_573000800	new	
alter	ALTER	Forban5929	Forban5929/Alter-FM1	FM-1_956	FM-1_956	new	
grainpiano	GRAIN PIANO	swtdr34mz	swtdr34mz-afk/GRAIN_PIANO	FM-1_572010300	FM-1_572010300	new	
tangram	Tangram	Mr. Roboto	Mr-Roboto/fm1-tangram	FM-1_975010000	FM-1_975010000	new	
shifl	shifl	Char Baba	schwartzworld/shifl	FM-1_24601	FM-1_24601	new	
opdream	OP dream	James	bertrandhustle2/skiff	FM-1_6731\\d{4}	FM-1_67310203	new	skiff
araminta	Araminta	Wulgaren	Wulgaren/Araminta	FM-1_4248	FM-1_4248	new	
sixmachines	SIX MACHINES	swtdr34mz	swtdr34mz-afk/SIX_MACHINES	FM-1_57400\\d{4}	FM-1_574000105	new	
offworld	OFFWORLD	swtdr34mz	swtdr34mz-afk/OFFWORLD	FM-1_57500\\d{4}	FM-1_575000402	new	
rhinotiger	RhinoTiger	hacha	hacha/rhinotiger	FM-1_1613010000	FM-1_1613010000	new	
abyss	ABYSS	Tentacle	Tentacletunes/abyss	FM-1_1300	FM-1_1300	new	
strangersynth	STRANGER SYNTH	swtdr34mz	swtdr34mz-afk/STRANGER_SYNTH	FM-1_57600\\d{4}	FM-1_576000700	new	
flat20	FLAT-20	Brandon Matthews	thenewwazoo/FLAT-20	FM-1_1978	FM-1_1978	new	dinghy
mb1	MB-1	Leonardo Lopez Chavira	leonardolopezchavira/minibrute-fm1	FM-1_4900\\d{4}	FM-1_49001500	new	
bourdon	BOURDON	Kevin Sint	kevinsint/bourdon	FM-1_20900\\d{2}	FM-1_2090035	new	
"""
# --- catalogue end ---


def entries():
    """the hub's list as dicts (FIELDS; base is a list of the ids it is built on)"""
    out = []
    for line in CATALOG.strip("\n").split("\n"):
        e = dict(zip(FIELDS, line.split("\t")))
        e["base"] = [b for b in e["base"].split("+") if b]
        out.append(e)
    return out


_COMPILED = None


def _compiled():
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = []
        for e in entries():
            try:
                _COMPILED.append((e, re.compile(e["ident"])))
            except re.error:
                _COMPILED.append((e, None))
    return _COMPILED


def broad(ident):
    """an identity pattern that takes any FM-1 number (Felucca's default, FM-1_\\d{3,8}): it names nothing"""
    return ident in (r"FM-1_\d{3,8}", r"FM-1_\d+")


def product_ids(text):
    """every "FM-1_NNN" identity in an app's bytes, in order, once each"""
    seen = []
    for m in re.finditer(rb"FM-1_(\d{3,10})(?!\d)", text):
        s = "FM-1_" + m.group(1).decode()
        if s not in seen:
            seen.append(s)
    return seen


def match(ids):
    """the hub's firmwares these identities point at, best first: an entry whose own build has exactly
    that identity, else one whose pattern takes it; patterns that take any number are left out"""
    exact, pattern = [], []
    for e, rx in _compiled():
        if any(e["product"] == i for i in ids):
            exact.append(e)
        elif rx is not None and not broad(e["ident"]) and any(rx.fullmatch(i) for i in ids):
            pattern.append(e)
    return exact or pattern


def label(e):
    return f"{e['name']}, by {e['author']}" if e.get("author") and e["author"] != e["name"] else e["name"]


def describe(found, most=4):
    """"Felucca, by Leo Kuroshita ..." for one, "one of: A, B, C and 2 more" for several, None for none"""
    if not found:
        return None
    if len(found) == 1:
        return label(found[0])
    names = [e["name"] for e in found]
    more = len(names) - most
    return "one of: " + ", ".join(names[:most]) + (f" and {more} more" if more > 0 else "")


def known(fid):
    """what this app knows about a firmware (KNOWN), every field UNKNOWN that is not known"""
    k = {"soft_key": UNKNOWN, "keys": UNKNOWN, "m_bootloader": UNKNOWN}
    k.update(KNOWN.get(fid, {}))
    return k


def from_catalog(catalog):
    """the rows of a downloaded hub catalogue (its "firmwares": FM-1 only, the SMK-37 list is apart)"""
    rows = []
    for o in catalog["firmwares"]:
        vals = [o.get("id") or "", o.get("name") or "", o.get("author") or "", o.get("repo") or "",
                o.get("ident") or "", o.get("product") or "", o.get("chip") or "", "+".join(o.get("base") or [])]
        rows.append("\t".join(v.replace("\t", " ").replace("\n", " ") for v in vals))
    return "\n".join(rows) + "\n"


def update(catalog_path, module=Path(__file__)):
    """rewrite this module's list from a catalogue file (the rest of the module is kept)"""
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    if catalog.get("license", {}).get("license") != CATALOG_LICENSE:
        raise SystemExit("the catalogue's licence is no longer " + CATALOG_LICENSE + ": check its terms first")
    src = module.read_text(encoding="utf-8")
    start, end = src.index(_BEGIN) + len(_BEGIN), src.index(_END)
    body = from_catalog(catalog).replace("\\", "\\\\")
    stamp = f'CATALOG_UPDATED = "{catalog.get("updated", "")}"\n'
    src = src[:start] + "\n" + stamp + 'CATALOG = """\\\n' + body + '"""\n' + src[end:]
    module.write_text(src, encoding="utf-8", newline="\n")
    return len(catalog["firmwares"])


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if a[:1] == ["--update"] and len(a) == 2:
        print(f"{update(a[1])} firmwares; {CATALOG_CREDIT}")
        return 0
    if not a:
        print(__doc__)
        return 2
    print(describe(match(a)) or UNKNOWN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
