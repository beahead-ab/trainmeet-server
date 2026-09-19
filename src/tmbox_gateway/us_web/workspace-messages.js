/* UI copy only: never translate issued authorities or imported meet content. */
(() => {
  const rows = [
    ['Close without saving changes?','Stäng utan att spara ändringarna?','Luk uden at gemme ændringerne?','Lukk uten å lagre endringene?','Ohne Speichern der Änderungen schließen?'],
    ['Menu','Meny','Menu','Meny','Menü'],
    ['Workspace menu','Arbetsytemeny','Arbejdsområdemenu','Arbeidsområdemeny','Arbeitsbereich-Menü'],
    ['Settings','Inställningar','Indstillinger','Innstillinger','Einstellungen'],
    ['Screens','Skärmar','Skærme','Skjermer','Anzeigen'],
    ['Change workspace','Byt arbetsyta','Skift arbejdsområde','Bytt arbeidsområde','Arbeitsbereich wechseln'],
    ['Log out','Logga ut','Log ud','Logg ut','Abmelden'],
    ['Workspace unavailable','Arbetsytan är inte tillgänglig','Arbejdsområdet er ikke tilgængeligt','Arbeidsområdet er ikke tilgjengelig','Arbeitsbereich nicht verfügbar'],
    ['This workspace is not available for the selected meet.','Den här arbetsytan är inte tillgänglig för den valda träffen.','Dette arbejdsområde er ikke tilgængeligt for det valgte træf.','Dette arbeidsområdet er ikke tilgjengelig for det valgte treffet.','Dieser Arbeitsbereich ist für das ausgewählte Treffen nicht verfügbar.'],
    ['Select a published meet in Server settings first.','Välj först en publicerad träff i serverns inställningar.','Vælg først et publiceret træf i serverens indstillinger.','Velg først et publisert treff i serverinnstillingene.','Zuerst ein veröffentlichtes Treffen in den Servereinstellungen auswählen.'],
    ['The selected config is changing. Waiting for the server to confirm the active session.','Vald config ändras. Väntar på att servern bekräftar den aktiva körningen.','Den valgte config ændres. Venter på, at serveren bekræfter den aktive kørsel.','Valgt config endres. Venter på at serveren bekrefter den aktive kjøringen.','Die ausgewählte Config wird geändert. Warten auf die Bestätigung der aktiven Betriebssitzung durch den Server.'],
    ['The server runs the meet selected in Cloud settings. Review its published config before starting.','Servern kör träffen som valts i Cloud-inställningarna. Granska publicerad config före start.','Serveren kører det træf, der er valgt i Cloud-indstillingerne. Gennemgå den publicerede config før start.','Serveren kjører treffet valgt i Cloud-innstillingene. Se gjennom publisert config før start.','Der Server betreibt das in den Cloud-Einstellungen ausgewählte Treffen. Vor dem Start die veröffentlichte Config prüfen.'],
    ['Selected meet config','Vald träffs config','Det valgte træfs config','Valgt treffs config','Config des ausgewählten Treffens'],
    ['Config is maintained in Cloud. Downloaded config remains available without internet.','Config ändras i Cloud. Hämtad config är tillgänglig även utan internet.','Config ændres i Cloud. Hentet config er også tilgængelig uden internet.','Config endres i Cloud. Hentet config er også tilgjengelig uten internett.','Config wird in Cloud gepflegt. Heruntergeladene Config bleibt ohne Internet verfügbar.'],
    ['The selected config is not ready yet. Check Cloud connection in Server settings.','Vald config är inte klar ännu. Kontrollera Cloud-kopplingen i serverns inställningar.','Den valgte config er ikke klar endnu. Kontrollér Cloud-forbindelsen i serverens indstillinger.','Valgt config er ikke klar ennå. Kontroller Cloud-tilkoblingen i serverinnstillingene.','Die ausgewählte Config ist noch nicht bereit. Cloud-Verbindung in den Servereinstellungen prüfen.'],
    ['The session clock starts paused. This server runs one selected meet.','Träffklockan startar pausad. Servern kör en enda vald träff.','Træfuret starter på pause. Serveren kører ét valgt træf.','Treffklokken starter på pause. Serveren kjører ett valgt treff.','Die Modellzeituhr startet angehalten. Dieser Server betreibt genau ein ausgewähltes Treffen.'],
    ['Source instructions','Instruktioner från underlaget','Instruktioner fra kildematerialet','Instruksjoner fra kildematerialet','Anweisungen aus den Unterlagen'],
    ['Read-only config. Changes are published in Cloud and applied safely by the server.','Config är skrivskyddad här. Ändringar publiceras i Cloud och tillämpas säkert av servern.','Config kan kun læses her. Ændringer publiceres i Cloud og anvendes sikkert af serveren.','Config er skrivebeskyttet her. Endringer publiseres i Cloud og tas i bruk trygt av serveren.','Config ist hier schreibgeschützt. Änderungen werden in Cloud veröffentlicht und vom Server sicher übernommen.']
  ];
  globalThis.TrainMeetMessages ||= {};
  for (const [en,sv,da,nb,de] of rows) globalThis.TrainMeetMessages[en]={en,sv,da,nb,de};
})();
